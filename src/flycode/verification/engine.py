"""Snapshot → registered runner → append-only evidence → deterministic verdict."""

import hashlib
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from ..domain.verification import (
    CheckAttempt,
    CheckSpec,
    EvidenceManifest,
    TrustMode,
    VerdictStatus,
    VerificationPlan,
    VerificationVerdict,
)
from .evidence import artifact_hashes, atomic_json, sanitize_outputs
from .runner import run_pytest
from .snapshot import capture, digest, input_files
from .verdict import evaluate


class VerificationEngine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def verify(self, repo: Path, *, timeout: float = 120., trust: TrustMode = 'trusted-local-process',
               reruns: int = 0, should_stop: Callable[[], bool] = lambda: False) -> VerificationVerdict:
        evidence_id = uuid4().hex
        directory = self.data_dir / evidence_id
        directory.mkdir()
        subject = directory / 'subject'
        snapshot = capture(repo, subject)
        plan = VerificationPlan(subject_hash=snapshot.subject_hash, contract_hash=digest({'profile': 'pytest-v1'}),
                                checks=[CheckSpec(timeout=timeout)], trust=trust, reruns=reruns)
        plan_hash = digest(plan.model_dump(mode='json'))
        atomic_json(directory / 'snapshot.json', snapshot.model_dump_json())
        atomic_json(directory / 'plan.json', plan.model_dump_json())
        attempts: list[CheckAttempt] = []
        invalidated, redacted = False, False
        status: VerdictStatus
        if trust == 'trusted-local-process':
            for number in range(1, reruns + 2):
                if should_stop() and attempts:
                    break
                sandbox = directory / f'sandbox-{number}'
                shutil.copytree(subject, sandbox)
                output = directory / f'attempt-{number}'
                try:
                    attempt = run_pytest(sandbox, output, timeout, number, should_stop)
                    attempts.append(attempt)
                    try:
                        files, _ = input_files(sandbox)
                        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
                        modes = {name: stat.S_IMODE((sandbox / name).stat().st_mode) for name in files}
                        invalidated |= hashes != snapshot.files or modes != snapshot.modes
                    except (OSError, ValueError):
                        invalidated = True
                    redacted |= sanitize_outputs(output)
                finally:
                    shutil.rmtree(sandbox)
                if attempt.cancelled or invalidated:
                    break
            status, reasons, cases = evaluate(attempts, invalidated)
            if should_stop() and status != 'invalidated':
                status, reasons = 'cancelled', [*reasons, 'Cancelled before all planned verification completed']
        else:
            status, reasons, cases = 'inconclusive', ['Required untrusted sandbox is unavailable; no runner started'], []
        manifest = EvidenceManifest(evidence_id=evidence_id, subject_hash=snapshot.subject_hash, plan_hash=plan_hash,
            trust=trust, environment=snapshot.environment, artifacts=artifact_hashes(directory),
            invocations=[attempt.model_dump(mode='json') for attempt in attempts],
            complete=len(attempts) == reruns + 1 and all(attempt.complete for attempt in attempts)
                     and status != 'cancelled', redacted=redacted,
            limitations=['Trusted local process only; project-supplied assertions are not an independent hostile-code oracle',
                         'Only registered pytest criteria are verified; application acceptance requires explicit oracle mapping'])
        atomic_json(directory / 'manifest.json', manifest.model_dump_json())
        verdict = VerificationVerdict(subject_hash=snapshot.subject_hash, plan_hash=plan_hash,
            environment=snapshot.environment, status=status, reasons=reasons, cases=cases,
            evidence_id=evidence_id, evidence_hash=hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
            required_coverage=1. if status == 'passed' else 0.)
        atomic_json(directory / 'verdict.json', verdict.model_dump_json())
        return verdict

    def current(self, verdict: VerificationVerdict, repo: Path) -> bool:
        directory = self.data_dir / verdict.evidence_id
        if not directory.resolve().is_relative_to(self.data_dir):
            return False
        try:
            stored = VerificationVerdict.model_validate_json((directory / 'verdict.json').read_text())
            if stored.model_dump(mode='json') != verdict.model_dump(mode='json'):
                return False
            manifest_path = directory / 'manifest.json'
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != verdict.evidence_hash:
                return False
            manifest = EvidenceManifest.model_validate_json(manifest_path.read_text())
            for name, expected in manifest.artifacts.items():
                path = (directory / name).resolve(strict=True)
                if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    return False
            return capture(repo).subject_hash == verdict.subject_hash
        except (OSError, ValueError):
            return False
