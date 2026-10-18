"""Registered suites execute the approved bytes/checks without inventing a plan."""

import json
import subprocess
from pathlib import Path

import pytest

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification import CheckSpec
from flycode.domain.verification_control import (
    VerificationPlanDraft,
    VerificationProfileDraft,
)
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store
from flycode.verification.custody import stopped
from flycode.verification.engine import VerificationEngine
from flycode.verification.registry import VerificationRegistry
from flycode.verification.snapshot import digest


def prepared(tmp_path, code, *, targets=(), reruns=0, timeout=10., git=False, check_id='selected'):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'test_answer.py').write_text(code)
    (source / 'payload.bin').write_bytes(b'\x00\xff original\r\n')
    if git:
        for arguments in [('init', '-q'), ('add', '.'),
                ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'Baseline')]:
            subprocess.run(['git', '-C', str(source), *arguments], check=True, capture_output=True)
        (source / 'untracked.bin').write_bytes(b'\x01 dirty bytes')
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(source, actor_id='owner')
        scope = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
        registry = VerificationRegistry(store)
        subject = registry.register_subject(scope, project.id)
        profile = registry.register_profile(scope, project.id, VerificationProfileDraft(title='Selected cases',
            checks=[CheckSpec(id=check_id, targets=list(targets), timeout=timeout)], reruns=reruns))
        record = registry.create_plan(scope, VerificationPlanDraft(subject_ids={'target': subject.id},
            profile_id=profile.id, profile_version=profile.version,
            budget={'max_tool_calls': 6, 'max_attempts': 3, 'wall_seconds': 900}, idempotency_key='plan'))
        root = registry.subject_root(scope, subject.id)
    finally:
        store.close()
    return VerificationEngine(tmp_path / 'evidence'), record.plan, root, source


def manifest(engine, verdict):
    return json.loads((engine.data_dir / verdict.evidence_id / 'manifest.json').read_text())


def test_registered_suite_keeps_plan_targets_and_every_diagnostic_rerun(tmp_path):
    engine, plan, root, source = prepared(tmp_path,
        'def test_selected(): assert True\ndef test_excluded(): assert False\n',
        targets=['test_answer.py::test_selected'], reruns=1)
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'passed' and result.scope == 'registered-suite', result.reasons
    assert result.plan_hash == digest(plan.model_dump(mode='json'))
    assert result.subject_hash == plan.subject_hash and result.required_coverage == 1
    assert result.checks == {'selected': 'passed'}
    bundle = engine.data_dir / result.evidence_id
    assert json.loads((bundle / 'plan.json').read_text()) == plan.model_dump(mode='json')
    evidence = manifest(engine, result)
    assert evidence['complete'] and len(evidence['invocations']) == 2
    assert [attempt['number'] for attempt in evidence['invocations']] == [1, 2]
    assert all(attempt['plan_id'] == plan.id and attempt['check_id'] == 'selected'
        and attempt['subject_hash'] == plan.subject_hash and attempt['stop_confirmed']
        for attempt in evidence['invocations'])
    assert {case.id for case in result.cases} == {'test_answer.py::test_selected'}
    assert all(case.check_id == 'selected' and case.subject_hash == plan.subject_hash for case in result.cases)
    assert (source / 'payload.bin').read_bytes() == b'\x00\xff original\r\n'
    assert engine.current(result, source)


@pytest.mark.parametrize(('code', 'status', 'outcome'), [
    ('def test_failure(): assert False', 'failed', 'failed'),
    ('# no cases', 'inconclusive', None),
    ('import pytest\n@pytest.mark.skip\ndef test_skip(): pass', 'inconclusive', 'skipped'),
    ('import pytest\n@pytest.mark.xfail\ndef test_known(): assert False', 'inconclusive', 'xfail'),
    ('def test_missing(unknown_fixture): pass', 'inconclusive', 'error'),
])
def test_registered_suite_uses_real_nonpassing_pytest_observations(tmp_path, code, status, outcome):
    engine, plan, root, _ = prepared(tmp_path, code)
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == status and result.required_coverage == 0, result.reasons
    assert ([case.outcome for case in result.cases] if outcome else result.cases) == ([outcome] if outcome else [])
    assert len(manifest(engine, result)['invocations']) == 1


def test_registered_suite_timeout_retains_actual_stopped_process(tmp_path):
    engine, plan, root, _ = prepared(tmp_path, 'import time\ndef test_slow(): time.sleep(60)', timeout=.5)
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'inconclusive'
    attempt = manifest(engine, result)['invocations'][0]
    assert attempt['error_class'] == 'timeout' and attempt['stop_confirmed']
    assert attempt['process_reason'] == 'timeout' and not attempt['complete']


def test_registered_suite_mutation_invalidates_without_changing_captured_or_owner_bytes(tmp_path):
    engine, plan, root, source = prepared(tmp_path,
        "from pathlib import Path\ndef test_mutate(): Path('payload.bin').write_bytes(b'changed')")
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'invalidated'
    assert not manifest(engine, result)['complete']
    for directory in (root, source):
        assert (directory / 'payload.bin').read_bytes() == b'\x00\xff original\r\n'


def test_registered_suite_preserves_first_failure_in_flaky_rerun(tmp_path):
    marker = tmp_path / 'flaky-state'
    engine, plan, root, _ = prepared(tmp_path,
        'from pathlib import Path\ndef test_flaky():\n'
        f'    p = Path({str(marker)!r})\n'
        '    old = p.exists()\n    p.touch()\n    assert old\n', reruns=1)
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'inconclusive'
    assert [case.outcome for case in result.cases] == ['failed', 'passed']
    assert all(case.flaky for case in result.cases)
    assert len(manifest(engine, result)['invocations']) == 2


def test_registered_suite_rejects_mutation_of_its_retained_evidence_subject(tmp_path):
    engine, plan, root, source = prepared(tmp_path,
        'from pathlib import Path\ndef test_evidence_mutation():\n'
        "    Path('../subject/payload.bin').write_bytes(b'changed retained input')\n")
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'invalidated' and result.required_coverage == 0
    assert not manifest(engine, result)['complete']
    assert all((directory / 'payload.bin').read_bytes() == b'\x00\xff original\r\n' for directory in (root, source))


@pytest.mark.parametrize('metadata', ['plan.json', 'snapshot.json'])
def test_registered_runner_cannot_publish_changed_plan_or_snapshot_metadata(tmp_path, metadata):
    engine, plan, root, source = prepared(tmp_path,
        'from pathlib import Path\ndef test_metadata():\n'
        f"    Path('../{metadata}').write_text('{{}}')\n")
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'invalidated' and not manifest(engine, result)['complete']
    assert not engine.current(result, source)


def test_registered_output_link_cannot_make_sanitization_edit_the_frozen_plan(tmp_path):
    engine, plan, root, _ = prepared(tmp_path,
        'from pathlib import Path\ndef test_output_link():\n'
        "    output = Path('../attempt-1/stdout.txt')\n"
        "    output.unlink()\n    output.symlink_to('../plan.json')\n", check_id='token=abc')
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'invalidated' and not manifest(engine, result)['complete']
    assert (engine.data_dir / result.evidence_id / 'plan.json').read_text() == plan.model_dump_json()


def test_registered_git_copy_explicitly_keeps_base_and_invalidates_owner_edits(tmp_path):
    engine, plan, root, source = prepared(tmp_path, 'def test_answer(): assert True', git=True)
    assert plan.subjects['target'].base is not None and not (root / '.git').exists()
    with pytest.raises(ValueError, match='subject'):
        engine.verify_plan(plan, {'target': root})
    result = engine.verify_plan(plan, {'target': root}, captured=True)
    assert result.status == 'passed' and engine.current(result, source)
    assert engine.current(result, root, captured=True)
    saved = json.loads((engine.data_dir / result.evidence_id / 'snapshot.json').read_text())
    assert saved == plan.subjects['target'].model_dump(mode='json')
    (source / 'payload.bin').write_bytes(b'edited owner')
    assert not engine.current(result, source)
    assert (root / 'payload.bin').read_bytes() == b'\x00\xff original\r\n'
    (root / 'payload.bin').write_bytes(b'changed registered copy')
    assert not engine.current(result, root, captured=True)


def test_registered_git_head_only_change_denies_fresh_execution_of_owner_root(tmp_path):
    engine, plan, root, source = prepared(tmp_path, 'def test_answer(): assert True', git=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
        'commit', '--allow-empty', '-qm', 'New base, same bytes'], check=True, capture_output=True)
    with pytest.raises(ValueError, match='subject'):
        engine.verify_plan(plan, {'target': source})
    assert not list(engine.data_dir.glob('*/plan.json'))
    result = engine.verify_plan(plan, {'target': root}, captured=True)
    assert result.status == 'passed' and not engine.current(result, source)


def test_registered_cancel_before_first_runner_retains_ids_without_process_admission(tmp_path):
    engine, plan, root, _ = prepared(tmp_path, 'def test_answer(): assert True')
    refs = []

    def execute(check, number, invocation):
        return invocation(on_prepare=refs.append, on_admit=refs.append)

    engine.invocation_executor = execute
    result = engine.verify_plan(plan, {'target': root}, should_stop=lambda: True)
    assert result.status == 'cancelled' and result.required_coverage == 0
    evidence = manifest(engine, result)
    assert not refs and not evidence['complete']
    attempt = evidence['invocations'][0]
    assert attempt['plan_id'] == plan.id and attempt['check_id'] == 'selected'
    assert attempt['cancelled'] and attempt['process'] is None and not attempt['process_started']


def test_registered_cancel_during_sleep_retains_prepared_identity_and_stopped_group(tmp_path):
    marker = tmp_path / 'running-marker'
    engine, plan, root, _ = prepared(tmp_path,
        'from pathlib import Path\nimport time\ndef test_slow():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    prepared_refs, admitted_refs = [], []

    def execute(check, number, invocation):
        def prepare(ref):
            assert not marker.exists()
            receipt = json.loads((Path(ref.directory) / 'process.json').read_text())
            assert receipt['state'] == 'preparing' and not receipt['started'] and receipt['pid'] is None
            prepared_refs.append(ref)

        def admit(ref):
            assert prepared_refs == [ref]
            admitted_refs.append(ref)

        return invocation(on_prepare=prepare, on_admit=admit)

    engine.invocation_executor = execute
    result = engine.verify_plan(plan, {'target': root}, should_stop=marker.exists)
    assert marker.exists() and result.status == 'cancelled'
    assert prepared_refs == admitted_refs and len(admitted_refs) == 1
    with stopped(admitted_refs[0]) as receipt:
        assert receipt.stop_confirmed and receipt.started and receipt.reason == 'cancelled'
    attempt = manifest(engine, result)['invocations'][0]
    assert attempt['cancelled'] and attempt['stop_confirmed'] and not manifest(engine, result)['complete']


@pytest.mark.parametrize('registered', [False, True])
def test_transient_stop_between_real_reruns_cannot_be_forgotten(tmp_path, registered):
    engine, plan, root, source = prepared(tmp_path, 'def test_answer(): assert True', reruns=1)
    stopped_once, ready = False, False

    def execute(check, number, invocation):
        nonlocal ready
        result = invocation()
        ready = True
        return result

    def should_stop():
        nonlocal stopped_once
        if ready and not stopped_once:
            stopped_once = True
            return True
        return False

    engine.invocation_executor = execute
    result = (engine.verify_plan(plan, {'target': root}, should_stop=should_stop) if registered
        else engine.verify(source, reruns=1, should_stop=should_stop))
    evidence = manifest(engine, result)
    assert stopped_once and len(evidence['invocations']) == 1
    assert result.status == 'cancelled' and result.required_coverage == 0
    assert not evidence['complete']


def test_current_pass_requires_a_complete_manifest_even_when_hashes_match(tmp_path):
    import hashlib

    engine, plan, root, source = prepared(tmp_path, 'def test_answer(): assert True')
    result = engine.verify_plan(plan, {'target': root})
    assert result.status == 'passed' and engine.current(result, source)
    bundle = engine.data_dir / result.evidence_id
    evidence = manifest(engine, result)
    evidence['complete'] = False
    content = json.dumps(evidence).encode()
    (bundle / 'manifest.json').write_bytes(content)
    changed = result.model_copy(update={'evidence_hash': hashlib.sha256(content).hexdigest()})
    (bundle / 'verdict.json').write_text(changed.model_dump_json())
    assert not engine.current(changed, source)


@pytest.mark.parametrize('metadata', [None, 'plan.json', 'snapshot.json'])
def test_captured_acceptance_keeps_git_provenance_and_external_owner_oracle(tmp_path, metadata):
    from flycode.domain.verification import (
        AcceptanceContract,
        AcceptanceCriterion,
        SubjectRef,
    )
    from flycode.verification.plan import PlanBuilder
    from flycode.verification.snapshot import capture

    engine, _, _, source = prepared(tmp_path, 'def test_project_case(): assert True', git=True)
    ref = SubjectRef(project_id='project', workflow_id='workflow', task_id='task', attempt_id='attempt')
    copy = tmp_path / 'captured'
    snapshot = capture(source, copy, ref=ref)
    oracle = tmp_path / 'owner-oracle'
    oracle.mkdir()
    code = (
        'from pathlib import Path\ndef test_bytes():\n'
        "    assert Path('payload.bin').read_bytes() == b'\\x00\\xff original\\r\\n'\n")
    if metadata is not None:
        code += f"    Path('../{metadata}').write_text('{{}}')\n"
    (oracle / 'test_oracle.py').write_text(code)
    checks = [CheckSpec(id=stage, stage=stage, targets=['test_oracle.py'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    contract = AcceptanceContract(id='original-bytes', version=1, criteria=[AcceptanceCriterion(id='bytes',
        check_ids=[check.id for check in checks], expected_case_ids=['test_oracle.py::test_bytes'])])
    approval = engine.oracles.approve(contract, checks, oracle)
    plan = PlanBuilder().build(snapshot, contract, checks,
        subjects={check.stage: snapshot for check in checks}, approval_hash=approval)
    roots = {check.stage: copy for check in checks}
    with pytest.raises(ValueError, match='subject'):
        engine.verify_plan(plan, roots)
    result = engine.verify_plan(plan, roots, captured=True)
    if metadata is not None:
        assert result.status == 'invalidated' and not engine.current(result, source, contract=contract)
        assert not manifest(engine, result)['complete']
        return
    assert result.status == 'passed' and result.scope == 'acceptance-contract', result.reasons
    assert result.required_coverage == 1 and result.criteria == {'bytes': 'passed'}
    assert engine.current(result, source, contract=contract)
    assert engine.current(result, copy, contract=contract, captured=True)
    for stage in roots:
        copied = engine.data_dir / result.evidence_id / 'subjects' / stage
        assert capture(copied, provenance=snapshot).subject_hash == snapshot.subject_hash
