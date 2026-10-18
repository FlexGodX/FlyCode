"""Snapshot → registered runner → append-only evidence → deterministic verdict."""

import hashlib
import shutil
from collections.abc import Callable
from functools import partial
from pathlib import Path
from uuid import uuid4

from ..domain.verification import (
    AcceptanceContract,
    CheckAttempt,
    CheckSpec,
    EvidenceManifest,
    SubjectSnapshot,
    TrustMode,
    VerdictStatus,
    VerificationPlan,
    VerificationVerdict,
)
from .acceptance import input_identity
from .evidence import (
    artifact_hashes,
    atomic_json,
    metadata_matches,
    read_regular,
    sanitize_outputs,
)
from .oracle import OracleRegistry
from .plan import contract_hash, validate_target
from .runner import run_pytest
from .snapshot import capture, digest
from .verdict import evaluate


def _latched_stop(callback: Callable[[], bool]) -> Callable[[], bool]:
    stopped = False

    def should_stop() -> bool:
        nonlocal stopped
        stopped = stopped or callback()
        return stopped

    return should_stop


class VerificationEngine:
    def __init__(self, data_dir: Path, *, invocation_executor: Callable[
            [CheckSpec, int, Callable[[], CheckAttempt]], CheckAttempt] | None = None) -> None:
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.oracles = OracleRegistry(self.data_dir / 'oracles')
        self.invocation_executor = invocation_executor

    def _invoke(self, check: CheckSpec, number: int, invocation: Callable[[], CheckAttempt]) -> CheckAttempt:
        return self.invocation_executor(check, number, invocation) if self.invocation_executor else invocation()

    def verify_plan(self, plan: VerificationPlan, roots: dict[str, Path], *,
                    should_stop: Callable[[], bool] = lambda: False,
                    captured: bool = False) -> VerificationVerdict:
        """Execute exact registered inputs; captured=True is for host-owned copies."""
        from .acceptance import verify_plan

        should_stop = _latched_stop(should_stop)
        plan = VerificationPlan.model_validate(plan.model_dump())
        if plan.scope == 'acceptance-contract':
            return verify_plan(self, plan, roots, should_stop, captured=captured)
        if (set(roots) != {'target'} or set(plan.subjects) != {'target'} or len(plan.checks) != 1
                or plan.acceptance is not None or plan.approval_hash is not None):
            raise ValueError('Registered suite requires one target subject and one mandatory check')
        check, snapshot = plan.checks[0], plan.subjects['target']
        if (not check.id or not check.required or check.stage != 'target' or check.dependencies
                or check.expected_outcome != 'passed' or check.version != 'check-v1'
                or check.oracle_version != 'pytest-observer+junit-v1'
                or check.subject_hash != snapshot.subject_hash or plan.subject_hash != snapshot.subject_hash):
            raise ValueError('Registered suite check or subject binding changed')
        for target in check.targets:
            validate_target(target)
        root = roots['target']
        if self.data_dir.is_relative_to(root.resolve()):
            raise ValueError('Evidence must be outside every subject')
        provenance = snapshot if captured else None
        if capture(root, ref=snapshot.ref, provenance=provenance).subject_hash != snapshot.subject_hash:
            raise ValueError('Registered subject bytes or environment changed')
        directory = self.data_dir / uuid4().hex
        directory.mkdir()
        copied = capture(root, directory / 'subject', ref=snapshot.ref, provenance=provenance)
        if copied.subject_hash != snapshot.subject_hash:
            raise ValueError('Registered subject changed during preparation')
        return self._suite(plan, snapshot, directory, should_stop)

    def verify(self, repo: Path, *, timeout: float = 120., trust: TrustMode = 'trusted-local-process',
               reruns: int = 0, should_stop: Callable[[], bool] = lambda: False) -> VerificationVerdict:
        evidence_id = uuid4().hex
        directory = self.data_dir / evidence_id
        directory.mkdir()
        subject = directory / 'subject'
        snapshot = capture(repo, subject)
        plan = VerificationPlan(subject_hash=snapshot.subject_hash, contract_hash=digest({'profile': 'pytest-v1'}),
                                checks=[CheckSpec(timeout=timeout)], trust=trust, reruns=reruns)
        return self._suite(plan, snapshot, directory, should_stop)

    def _suite(self, plan: VerificationPlan, snapshot: SubjectSnapshot, directory: Path,
               should_stop: Callable[[], bool]) -> VerificationVerdict:
        should_stop = _latched_stop(should_stop)
        evidence_id, subject, check = directory.name, directory / 'subject', plan.checks[0]
        plan_hash = digest(plan.model_dump(mode='json'))
        atomic_json(directory / 'snapshot.json', snapshot.model_dump_json())
        atomic_json(directory / 'plan.json', plan.model_dump_json())
        metadata = {'snapshot.json': snapshot.model_dump_json(), 'plan.json': plan.model_dump_json()}
        attempts: list[CheckAttempt] = []
        invalidated, redacted = False, False
        status: VerdictStatus
        if plan.trust == 'trusted-local-process':
            for number in range(1, plan.reruns + 2):
                if should_stop() and attempts:
                    break
                sandbox = directory / f'sandbox-{number}'
                shutil.copytree(subject, sandbox)
                output = directory / f'attempt-{number}'
                try:
                    attempt = self._invoke(check, number,
                        partial(run_pytest, sandbox, output, timeout=check.timeout, number=number,
                            should_stop=should_stop, targets=check.targets))
                    attempt = attempt.model_copy(update={'check_id': check.id, 'plan_id': plan.id,
                        'subject_hash': snapshot.subject_hash, 'cases': [case.model_copy(update={
                            'check_id': check.id, 'subject_hash': snapshot.subject_hash}) for case in attempt.cases]})
                    attempts.append(attempt)
                    try:
                        expected = snapshot.files, snapshot.modes
                        invalidated |= input_identity(sandbox) != expected or input_identity(subject) != expected
                        redacted |= sanitize_outputs(output)
                        invalidated |= not metadata_matches(directory, metadata)
                    except (OSError, ValueError):
                        invalidated = True
                finally:
                    shutil.rmtree(sandbox)
                if attempt.cancelled or invalidated:
                    break
            status, reasons, cases = evaluate(attempts, invalidated)
            if should_stop() and status != 'invalidated':
                status, reasons = 'cancelled', [*reasons, 'Cancelled before all planned verification completed']
            if status == 'passed' and (len(attempts) != plan.reruns + 1 or not all(attempt.complete for attempt in attempts)):
                status, reasons = 'inconclusive', [*reasons, 'Required verification invocations are incomplete']
        else:
            status, reasons, cases = 'inconclusive', ['Required untrusted sandbox is unavailable; no runner started'], []
        manifest = EvidenceManifest(evidence_id=evidence_id, subject_hash=snapshot.subject_hash, plan_hash=plan_hash,
            trust=plan.trust, environment=snapshot.environment, artifacts=artifact_hashes(directory),
            invocations=[attempt.model_dump(mode='json') for attempt in attempts],
            complete=len(attempts) == plan.reruns + 1 and all(attempt.complete for attempt in attempts)
                     and status not in {'cancelled', 'invalidated'}, redacted=redacted,
            limitations=['Trusted local process only; project-supplied assertions are not an independent hostile-code oracle',
                         'Only registered pytest criteria are verified; application acceptance requires explicit oracle mapping'])
        atomic_json(directory / 'manifest.json', manifest.model_dump_json())
        verdict = VerificationVerdict(subject_hash=snapshot.subject_hash, plan_hash=plan_hash,
            environment=snapshot.environment, status=status, reasons=reasons, cases=cases,
            evidence_id=evidence_id, evidence_hash=hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
            required_coverage=1. if status == 'passed' else 0., checks={check.id: status})
        atomic_json(directory / 'verdict.json', verdict.model_dump_json())
        return verdict

    def current(self, verdict: VerificationVerdict, repo: Path, *, contract: AcceptanceContract | None = None,
                captured: bool = False) -> bool:
        directory = self.data_dir / verdict.evidence_id
        if not directory.resolve().is_relative_to(self.data_dir):
            return False
        try:
            stored = VerificationVerdict.model_validate_json(read_regular(directory / 'verdict.json'))
            if stored.model_dump(mode='json') != verdict.model_dump(mode='json'):
                return False
            manifest_path = directory / 'manifest.json'
            manifest_bytes = read_regular(manifest_path)
            if hashlib.sha256(manifest_bytes).hexdigest() != verdict.evidence_hash:
                return False
            manifest = EvidenceManifest.model_validate_json(manifest_bytes)
            if verdict.status == 'passed' and not manifest.complete:
                return False
            for name, expected in manifest.artifacts.items():
                path = (directory / name).resolve(strict=True)
                if not path.is_relative_to(directory) or hashlib.sha256(read_regular(directory / name)).hexdigest() != expected:
                    return False
            snapshot = SubjectSnapshot.model_validate_json(read_regular(directory / 'snapshot.json'))
            plan = VerificationPlan.model_validate_json(read_regular(directory / 'plan.json'))
            identity = snapshot.model_dump(include={'files', 'modes', 'base', 'environment'}, mode='json')
            if snapshot.ref is not None:
                identity['ref'] = snapshot.ref.model_dump(mode='json')
            if (digest(identity) != snapshot.subject_hash or snapshot.subject_hash != verdict.subject_hash
                    or digest(plan.model_dump(mode='json')) != verdict.plan_hash or plan.scope != verdict.scope
                    or plan.subject_hash != verdict.subject_hash or manifest.subject_hash != verdict.subject_hash
                    or manifest.plan_hash != verdict.plan_hash or manifest.trust != plan.trust
                    or digest(manifest.environment) != digest(snapshot.environment)
                    or digest(verdict.environment) != digest(snapshot.environment)):
                return False
            if plan.subjects:
                stage = 'integration' if plan.scope == 'acceptance-contract' else 'target'
                if plan.subjects[stage].model_dump(mode='json') != snapshot.model_dump(mode='json'):
                    return False
            if verdict.scope == 'acceptance-contract':
                self.oracles.validate(plan)
                if (verdict.contract_hash != plan.contract_hash
                        or (contract is not None and contract_hash(contract) != verdict.contract_hash)):
                    return False
            elif contract is not None:
                return False
            return capture(repo, ref=snapshot.ref, provenance=snapshot if captured else None).subject_hash == verdict.subject_hash
        except (OSError, ValueError, KeyError):
            return False
