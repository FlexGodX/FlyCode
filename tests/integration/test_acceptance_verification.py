"""Acceptance runs use owner-approved tests on baseline, patch and integrated bytes."""


import pytest


def prepared(tmp_path):
    from flycode.domain.verification import (
        AcceptanceContract,
        AcceptanceCriterion,
        CheckSpec,
        SubjectRef,
    )
    from flycode.verification import VerificationEngine
    from flycode.verification.plan import PlanBuilder
    from flycode.verification.snapshot import capture

    oracle = tmp_path / 'trusted-oracle'
    oracle.mkdir()
    (oracle / 'test_oracle.py').write_text('from product import add\n'
        'def test_existing(): assert add(0, 0) == 0\n'
        'def test_behavior(): assert add(2, 3) == 5\n')
    roots = {}
    for stage in ('baseline', 'target', 'regression', 'integration'):
        root = tmp_path / stage
        root.mkdir()
        operator = '-' if stage == 'baseline' else '+'
        (root / 'product.py').write_text(f'def add(a, b): return a {operator} b\n')
        roots[stage] = root
    checks = [
        CheckSpec(id='baseline', stage='baseline', targets=['test_oracle.py::test_existing']),
        CheckSpec(id='negative', stage='baseline', targets=['test_oracle.py::test_behavior'],
                  expected_outcome='failed', dependencies=['baseline']),
        CheckSpec(id='target', stage='target', targets=['test_oracle.py::test_behavior'],
                  dependencies=['negative']),
        CheckSpec(id='regression', stage='regression', targets=['test_oracle.py'], dependencies=['target']),
        CheckSpec(id='integration', stage='integration', targets=['test_oracle.py'], dependencies=['regression']),
    ]
    contract = AcceptanceContract(id='addition', version=1, criteria=[
        AcceptanceCriterion(id='R1', check_ids=['negative', 'target', 'regression', 'integration'],
                            expected_case_ids=['test_oracle.py::test_behavior']),
        AcceptanceCriterion(id='R2', check_ids=['baseline', 'regression', 'integration'],
                            expected_case_ids=['test_oracle.py::test_existing']),
    ])
    engine = VerificationEngine(tmp_path / 'evidence')
    approved = engine.oracles.approve(contract, checks, oracle)
    ref = SubjectRef(project_id='p', workflow_id='w', task_id='t', attempt_id='a')
    snapshots = {stage: capture(root, ref=ref) for stage, root in roots.items()}
    plan = PlanBuilder().build(snapshots['integration'], contract, checks, subjects=snapshots,
                               approval_hash=approved)
    return engine, plan, roots


def test_real_baseline_negative_target_regression_and_integration(tmp_path):
    engine, plan, roots = prepared(tmp_path)
    result = engine.verify_plan(plan, roots)
    assert result.status == 'passed', result.reasons
    assert result.scope == 'acceptance-contract'
    assert result.required_coverage == 1
    assert result.criteria == {'R1': 'passed', 'R2': 'passed'}
    assert any(case.outcome == 'failed' for case in result.cases)  # Raw negative control is retained.
    assert engine.current(result, roots['integration'], contract=plan.acceptance)
    assert not engine.current(result, roots['integration'], contract=plan.acceptance.model_copy(update={'version': 2}))
    assert engine.current(result, roots['integration'], contract=plan.acceptance.model_copy(update={'title': 'Display'}))
    evidence = engine.data_dir / result.evidence_id
    import json
    manifest = json.loads((evidence / 'manifest.json').read_text())
    attempts = manifest['invocations']
    assert {attempt['check_id'] for attempt in attempts} == {check.id for check in plan.checks}
    assert all(attempt['state'] == 'finished' and attempt['plan_id'] == plan.id for attempt in attempts)
    assert next(attempt for attempt in attempts if attempt['check_id'] == 'negative')['error_class'] == 'assertion'
    assert len({attempt['id'] for attempt in attempts}) == len(attempts)
    events = [json.loads(line) for line in (evidence / 'events.jsonl').read_text().splitlines()]
    assert events[0]['state'] == 'preparing' and events[-1]['state'] == 'completed'
    assert any(event['state'] == 'running' for event in events)
    assert all('lease' not in attempt for attempt in attempts)


@pytest.mark.parametrize('tamper', ['remove_check', 'criteria', 'profile', 'oracle_bytes', 'subject'])
def test_tampering_is_rejected_before_subprocess(tmp_path, monkeypatch, tamper):
    engine, plan, roots = prepared(tmp_path)
    if tamper == 'remove_check':
        plan = plan.model_copy(update={'checks': [check for check in plan.checks if check.id != 'regression']})
    elif tamper == 'criteria':
        plan.acceptance.criteria[0].expected_case_ids.append('test_oracle.py::forged')
    elif tamper == 'profile':
        plan.checks[0].targets.append('--override-ini=anything')
    elif tamper == 'oracle_bytes':
        next((engine.oracles.directory / plan.approval_hash).rglob('test_oracle.py')).write_text('def test_fake(): pass')
    else:
        (roots['target'] / 'product.py').write_text('def add(a, b): return 42')
    calls = []
    monkeypatch.setattr('flycode.verification.engine.run_pytest', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        engine.verify_plan(plan, roots)
    assert not calls


def test_green_target_does_not_hide_broken_integration(tmp_path):
    from flycode.verification.snapshot import capture

    engine, plan, roots = prepared(tmp_path)
    (roots['integration'] / 'product.py').write_text('def add(a, b): return a - b')
    integrated = capture(roots['integration'], ref=plan.subjects['integration'].ref)
    checks = [check.model_copy(update={'subject_hash': integrated.subject_hash}) if check.stage == 'integration'
              else check for check in plan.checks]
    plan = plan.model_copy(update={'subject_hash': integrated.subject_hash, 'checks': checks,
                                  'subjects': {**plan.subjects, 'integration': integrated}})
    result = engine.verify_plan(plan, roots)
    assert result.status == 'failed'
    assert result.criteria['R1'] == 'failed'
    assert result.checks['target'] == 'passed'
    assert result.checks['integration'] == 'failed'
    assert result.required_coverage < 1


def test_agent_test_edits_cannot_replace_trusted_oracle(tmp_path):
    from flycode.verification.snapshot import capture

    engine, plan, roots = prepared(tmp_path)
    root = roots['target']
    (root / 'product.py').write_text('def add(a, b): return a - b')
    (root / 'test_oracle.py').write_text('def test_behavior(): assert True')
    target = capture(root, ref=plan.subjects['target'].ref)
    plan = plan.model_copy(update={'checks': [check.model_copy(update={'subject_hash': target.subject_hash})
        if check.stage == 'target' else check for check in plan.checks],
        'subjects': {**plan.subjects, 'target': target}})
    result = engine.verify_plan(plan, roots)
    assert result.status == 'failed'
    assert result.checks['target'] == 'failed'
    assert (root / 'test_oracle.py').read_text() == 'def test_behavior(): assert True'


def test_suite_verdict_has_no_acceptance_scope(tmp_path):
    from flycode.verification import VerificationEngine

    root = tmp_path / 'project'
    root.mkdir()
    (root / 'test_ok.py').write_text('def test_ok(): assert True')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'passed' and result.scope == 'registered-suite'
    assert not result.contract_hash


def test_new_owner_contract_invalidates_previous_approval(tmp_path):
    engine, plan, roots = prepared(tmp_path)
    result = engine.verify_plan(plan, roots)
    assert engine.current(result, roots['integration'], contract=plan.acceptance)
    revised = plan.acceptance.model_copy(update={'version': 2})
    approved = engine.oracles.read(plan.approval_hash)
    engine.oracles.approve(revised, approved.checks, engine.oracles.directory / plan.approval_hash / 'files')
    assert not engine.current(result, roots['integration'])
    with pytest.raises(ValueError):
        engine.verify_plan(plan, roots)


def test_junit_outcomes_must_agree_with_observed_case_outcomes(tmp_path):
    from flycode.verification import VerificationEngine

    root = tmp_path / 'project'
    root.mkdir()
    (root / 'test_ok.py').write_text('def test_ok(): assert True')
    (root / 'conftest.py').write_text('''from pathlib import Path
import pytest
@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    Path(session.config.option.xmlpath).write_text('<testsuites><testsuite tests="1">'
        '<testcase classname="test_ok" name="test_ok"><failure message="forged"/>'
        '</testcase></testsuite></testsuites>')
''')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'inconclusive'
    assert any('mismatch' in reason.lower() for reason in result.reasons)


def test_broken_baseline_cannot_be_hidden_by_green_patch(tmp_path):
    from flycode.verification.snapshot import capture

    engine, plan, roots = prepared(tmp_path)
    (roots['baseline'] / 'product.py').write_text('def add(a, b): return 1')
    baseline = capture(roots['baseline'], ref=plan.subjects['baseline'].ref)
    plan = plan.model_copy(update={'checks': [check.model_copy(update={'subject_hash': baseline.subject_hash})
        if check.stage == 'baseline' else check for check in plan.checks],
        'subjects': {**plan.subjects, 'baseline': baseline}})
    result = engine.verify_plan(plan, roots)
    assert result.status == 'failed'
    assert result.checks['baseline'] == 'failed'
    assert result.checks['target'] == 'inconclusive'
    assert result.required_coverage == 0


def test_optional_failure_is_retained_without_replacing_required_criteria(tmp_path):
    from flycode.domain.verification import CheckSpec
    from flycode.verification.plan import PlanBuilder

    engine, plan, roots = prepared(tmp_path)
    (tmp_path / 'trusted-oracle' / 'test_optional.py').write_text('def test_extra(): assert False')
    extra = CheckSpec(id='extra', required=False, stage='target', targets=['test_optional.py'])
    approved = engine.oracles.approve(plan.acceptance, plan.checks, tmp_path / 'trusted-oracle')
    plan = PlanBuilder().build(plan.subjects['integration'], plan.acceptance, plan.checks,
        subjects=plan.subjects, approval_hash=approved, proposals=[extra])
    result = engine.verify_plan(plan, roots)
    assert result.status == 'passed', result.reasons
    assert result.checks['extra'] == 'failed'
    assert result.required_coverage == 1
    assert any(case.check_id == 'extra' and case.outcome == 'failed' for case in result.cases)


def test_missing_required_case_remains_in_coverage_denominator(tmp_path):
    from flycode.verification.plan import PlanBuilder

    engine, plan, roots = prepared(tmp_path)
    criterion = plan.acceptance.criteria[0].model_copy(update={'expected_case_ids': ['test_oracle.py::test_absent']})
    contract = plan.acceptance.model_copy(update={'version': 2, 'criteria': [criterion, plan.acceptance.criteria[1]]})
    approval = engine.oracles.approve(contract, plan.checks, tmp_path / 'trusted-oracle')
    plan = PlanBuilder().build(plan.subjects['integration'], contract, plan.checks, subjects=plan.subjects,
                               approval_hash=approval)
    result = engine.verify_plan(plan, roots)
    assert result.status == 'inconclusive'
    assert result.criteria == {'R1': 'inconclusive', 'R2': 'passed'}
    assert result.required_coverage == .5
    assert any('R1' in reason for reason in result.reasons)
    assert (engine.data_dir / result.evidence_id / 'oracle' / 'test_oracle.py').is_file()


def test_new_git_base_invalidates_even_when_input_file_bytes_are_unchanged(tmp_path):
    import subprocess

    from flycode.verification.snapshot import capture

    engine, plan, roots = prepared(tmp_path)
    root = roots['integration']
    git = ['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
           '-c', 'commit.gpgsign=false']
    subprocess.run([*git, 'init', '-q'], check=True, capture_output=True)
    subprocess.run([*git, 'add', '.'], check=True)
    subprocess.run([*git, 'commit', '-qm', 'baseline'], check=True)
    integrated = capture(root, ref=plan.subjects['integration'].ref)
    plan = plan.model_copy(update={'subject_hash': integrated.subject_hash,
        'checks': [check.model_copy(update={'subject_hash': integrated.subject_hash})
                   if check.stage == 'integration' else check for check in plan.checks],
        'subjects': {**plan.subjects, 'integration': integrated}})
    result = engine.verify_plan(plan, roots)
    assert result.status == 'passed' and engine.current(result, root, contract=plan.acceptance)
    subprocess.run([*git, 'commit', '--allow-empty', '-qm', 'new base'], check=True)
    assert capture(root, ref=integrated.ref).files == integrated.files
    assert not engine.current(result, root, contract=plan.acceptance)
    with pytest.raises(ValueError, match='subject'):
        engine.verify_plan(plan, roots)


def test_unconfirmed_cleanup_preserves_cases_but_cannot_pass(tmp_path, monkeypatch):
    import json

    from flycode.verification import VerificationEngine

    root = tmp_path / 'project'
    root.mkdir()
    (root / 'test_ok.py').write_text('def test_ok(): assert True')

    def unconfirmed(process):
        raise PermissionError('cleanup not confirmed')

    monkeypatch.setattr('flycode.verification.runner._terminate', unconfirmed)
    engine = VerificationEngine(tmp_path / 'evidence')
    result = engine.verify(root)
    assert result.status == 'inconclusive'
    assert result.cases[0].outcome == 'passed'
    manifest = json.loads((engine.data_dir / result.evidence_id / 'manifest.json').read_text())
    assert not manifest['complete']
    attempt = manifest['invocations'][0]
    assert attempt['error_class'] == 'cleanup' and attempt['state'] == 'interrupted'
