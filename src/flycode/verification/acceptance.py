"""Execute a staged acceptance graph using external owner-approved oracle bytes."""

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from ..domain.verification import (
    CheckAttempt,
    EvidenceManifest,
    VerdictStatus,
    VerificationPlan,
    VerificationVerdict,
)
from .evidence import (
    _regular,
    artifact_hashes,
    atomic_json,
    metadata_matches,
    sanitize_outputs,
)
from .plan import STAGE_ORDER
from .snapshot import capture, digest, input_files
from .verdict import evaluate_contract

if TYPE_CHECKING:
    from .engine import VerificationEngine


def input_identity(root: Path) -> tuple[dict[str, str], dict[str, int]]:
    files, _ = input_files(root)
    return ({name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            {name: stat.S_IMODE((root / name).stat().st_mode) for name in files})


def verify_plan(engine: 'VerificationEngine', plan: VerificationPlan, roots: dict[str, Path],
                should_stop: Callable[[], bool], *, captured: bool = False) -> VerificationVerdict:
    plan = VerificationPlan.model_validate(plan.model_dump())
    engine.oracles.validate(plan)
    assert plan.acceptance is not None and plan.approval_hash is not None
    if set(roots) != set(plan.subjects):
        raise ValueError('All staged subject roots are required')
    for stage, root in roots.items():
        if engine.data_dir.is_relative_to(root.resolve()):
            raise ValueError('Evidence must be outside every subject')
        snapshot = plan.subjects[stage]
        provenance = snapshot if captured else None
        if capture(root, ref=snapshot.ref, provenance=provenance).subject_hash != snapshot.subject_hash:
            raise ValueError(f'{stage} subject bytes or environment changed')
    evidence_id = uuid4().hex
    directory = engine.data_dir / evidence_id
    directory.mkdir()
    sequence = 0
    journal = directory / 'events.jsonl'
    atomic_json(journal, '')
    journal_info = journal.lstat()

    def event(state: str, check_id: str | None = None, attempt_id: str | None = None):
        nonlocal sequence, journal_info
        sequence += 1
        payload = {'id': sequence, 'state': state, 'check_id': check_id, 'attempt_id': attempt_id,
                   'timestamp': datetime.now(timezone.utc).isoformat()}
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns')
        with _regular(journal, writable=True) as stream:
            if any(getattr(journal_info, field) != getattr(os.fstat(stream.fileno()), field) for field in fields):
                raise ValueError('Verification journal changed')
            stream.seek(0, os.SEEK_END)
            stream.write((json.dumps(payload) + '\n').encode('utf-8'))
            stream.flush()
            journal_info = os.fstat(stream.fileno())
            if any(getattr(journal_info, field) != getattr(journal.lstat(), field) for field in fields):
                raise ValueError('Verification journal changed')

    event('preparing')
    atomic_json(directory / 'plan.json', plan.model_dump_json())
    atomic_json(directory / 'snapshot.json', plan.subjects['integration'].model_dump_json())
    atomic_json(directory / 'oracle.json', engine.oracles.read(plan.approval_hash).model_dump_json())
    metadata = {'plan.json': plan.model_dump_json(), 'snapshot.json': plan.subjects['integration'].model_dump_json(),
        'oracle.json': engine.oracles.read(plan.approval_hash).model_dump_json()}
    oracle = directory / 'oracle'
    oracle.mkdir()
    engine.oracles.overlay(plan.approval_hash, oracle)
    for stage, root in roots.items():
        provenance = plan.subjects[stage] if captured else None
        snapshot = capture(root, directory / 'subjects' / stage, ref=plan.subjects[stage].ref, provenance=provenance)
        if snapshot.subject_hash != plan.subjects[stage].subject_hash:
            raise ValueError('Subject changed during preparation')
    attempts: list[CheckAttempt] = []
    remaining = list(plan.checks)
    outcomes: dict[str, VerdictStatus] = {}
    invalidated, redacted = False, False
    if plan.trust == 'untrusted':
        remaining = []
    while remaining:
        check = min((item for item in remaining if all(dependency in outcomes for dependency in item.dependencies)),
                    key=lambda item: STAGE_ORDER[item.stage])
        remaining.remove(check)
        if should_stop():
            attempts.append(CheckAttempt(number=1, check_id=check.id, plan_id=plan.id, subject_hash=check.subject_hash,
                argv=[], exit_code=None, duration=0, cancelled=True, state='cancelled', error_class='cancelled',
                reasons=['Cancelled before check start']))
            break
        blocked = [dependency for dependency in check.dependencies if outcomes[dependency] != 'passed']
        if blocked:
            attempts.append(CheckAttempt(number=1, check_id=check.id, plan_id=plan.id, subject_hash=check.subject_hash,
                argv=[], exit_code=None, duration=0, state='blocked', error_class='policy',
                reasons=[f'Dependency did not meet its contract: {dependency}' for dependency in blocked]))
            event('blocked', check.id)
        else:
            for number in range(1, plan.reruns + 2):
                attempt_id = uuid4().hex
                sandbox = directory / f'sandbox-{attempt_id}'
                output = directory / f'attempt-{attempt_id}'
                event('queued', check.id, attempt_id)
                shutil.copytree(directory / 'subjects' / check.stage, sandbox)
                try:
                    shutil.copytree(oracle, sandbox, dirs_exist_ok=True)
                    expected = input_identity(sandbox)
                    # Use the same registered adapter as standalone suite verification.
                    from .engine import run_pytest

                    attempt = engine._invoke(check, number, partial(run_pytest,
                        sandbox, output, timeout=check.timeout, number=number, should_stop=should_stop, targets=check.targets,
                        on_state=partial(event, check_id=check.id, attempt_id=attempt_id)))
                    attempt = attempt.model_copy(update={'id': attempt_id, 'check_id': check.id, 'plan_id': plan.id,
                        'subject_hash': check.subject_hash, 'cases': [case.model_copy(update={
                            'check_id': check.id, 'subject_hash': check.subject_hash}) for case in attempt.cases]})
                    attempts.append(attempt)
                    try:
                        invalidated |= input_identity(sandbox) != expected
                        redacted |= sanitize_outputs(output)
                        invalidated |= not metadata_matches(directory, metadata)
                    except (OSError, ValueError):
                        invalidated = True
                    event(attempt.state, check.id, attempt_id)
                finally:
                    shutil.rmtree(sandbox)
                if attempt.cancelled or invalidated:
                    break
        assessment = evaluate_contract(attempts, invalidated, plan.acceptance, plan.checks)
        outcomes[check.id] = assessment.checks[check.id]
        if invalidated or any(attempt.cancelled for attempt in attempts):
            break
    for stage, snapshot in plan.subjects.items():
        invalidated |= input_identity(directory / 'subjects' / stage) != (snapshot.files, snapshot.modes)
    engine.oracles.validate(plan)
    event('evaluating')
    assessment = evaluate_contract(attempts, invalidated, plan.acceptance, plan.checks)
    if plan.trust == 'untrusted':
        assessment = assessment.model_copy(update={'status': 'inconclusive',
            'reasons': [*assessment.reasons, 'Required untrusted sandbox is unavailable; no runner started']})
    if should_stop() and assessment.status != 'invalidated':
        assessment = assessment.model_copy(update={'status': 'cancelled',
            'reasons': [*assessment.reasons, 'Cancelled before all planned checks completed']})
    event('cancelled' if assessment.status == 'cancelled' else 'completed')
    plan_hash = digest(plan.model_dump(mode='json'))
    environment = plan.subjects['integration'].environment
    complete = all(len([attempt for attempt in attempts if attempt.check_id == check.id and attempt.complete])
                   == plan.reruns + 1 for check in plan.checks if check.required)
    manifest = EvidenceManifest(evidence_id=evidence_id, subject_hash=plan.subject_hash, plan_hash=plan_hash,
        trust=plan.trust, environment=environment, artifacts=artifact_hashes(directory),
        invocations=[attempt.model_dump(mode='json') for attempt in attempts],
        complete=complete and assessment.status not in {'cancelled', 'invalidated'}
                 and not any(attempt.error_class == 'cleanup' for attempt in attempts), redacted=redacted,
        limitations=['Trusted local process only; no hostile-code sandbox',
                     'Only explicitly approved criteria and registered pytest oracle are covered'])
    atomic_json(directory / 'manifest.json', manifest.model_dump_json())
    verdict = VerificationVerdict(version='verification-verdict-v2', subject_hash=plan.subject_hash,
        plan_hash=plan_hash, environment=environment, status=assessment.status, reasons=assessment.reasons,
        cases=assessment.cases, evidence_id=evidence_id,
        evidence_hash=hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest(),
        required_coverage=assessment.coverage, scope='acceptance-contract', contract_hash=plan.contract_hash,
        criteria=assessment.criteria, checks=assessment.checks, evaluator_version='acceptance-verdict-v1')
    atomic_json(directory / 'verdict.json', verdict.model_dump_json())
    return verdict
