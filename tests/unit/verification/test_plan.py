"""Trusted requirements must bind exact checks, cases, stages and subject scope."""

import pytest

from flycode.domain.verification import CaseResult, CheckAttempt


def contract():
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion

    return AcceptanceContract(id='arithmetic', version=1, criteria=[AcceptanceCriterion(
        id='R1', check_ids=['target', 'regression', 'integration'],
        expected_case_ids=['test_oracle.py::test_behavior'])])


def checks():
    from flycode.domain.verification import CheckSpec

    return [CheckSpec(id=stage, stage=stage, targets=['test_oracle.py'],
                      dependencies=[] if stage == 'baseline' else ['baseline'])
            for stage in ('baseline', 'target', 'regression', 'integration')]


def subjects(tmp_path):
    from flycode.domain.verification import SubjectRef
    from flycode.verification.snapshot import capture

    root = tmp_path / 'project'
    root.mkdir()
    (root / 'product.py').write_text('VALUE = 1')
    snapshot = capture(root, ref=SubjectRef(project_id='p', workflow_id='w', task_id='t', attempt_id='a'))
    return {stage: snapshot for stage in ('baseline', 'target', 'regression', 'integration')}


def test_unrelated_green_test_does_not_cover_requirement():
    from flycode.verification.verdict import evaluate

    attempt = CheckAttempt(number=1, check_id='target', argv=['pytest'], exit_code=0, duration=1,
        cases=[CaseResult(id='test_oracle.py::test_other', outcome='passed')],
        collected=['test_oracle.py::test_other'], complete=True)
    status, reasons, _ = evaluate([attempt], invalidated=False, contract=contract(), checks=checks())
    assert status == 'inconclusive'
    assert any('R1' in reason for reason in reasons)


def test_builder_binds_stages_and_exact_snapshots(tmp_path):
    from flycode.verification.plan import PlanBuilder

    snapshots = subjects(tmp_path)
    plan = PlanBuilder().build(snapshots['integration'], contract(), checks(), subjects=snapshots,
                               approval_hash='a' * 64)
    assert plan.scope == 'acceptance-contract'
    assert {check.subject_hash for check in plan.checks} == {plan.subject_hash}
    assert plan.contract_hash
    assert plan.subjects['integration'].ref.task_id == 't'


@pytest.mark.parametrize('fault', ['missing_check', 'dependency_cycle', 'foreign_scope', 'unknown_profile'])
def test_invalid_plans_are_rejected_before_execution(tmp_path, fault):
    from flycode.domain.verification import SubjectRef
    from flycode.verification.plan import PlanBuilder

    snapshots, registered = subjects(tmp_path), checks()
    if fault == 'missing_check':
        registered = [check for check in registered if check.id != 'regression']
    elif fault == 'dependency_cycle':
        registered[0] = registered[0].model_copy(update={'dependencies': ['target']})
    elif fault == 'foreign_scope':
        snapshots['baseline'] = snapshots['baseline'].model_copy(update={'ref': SubjectRef(
            project_id='foreign', workflow_id='w', task_id='t', attempt_id='a')})
    else:
        registered[0] = registered[0].model_copy(update={'profile': 'shell'})
    with pytest.raises(ValueError):
        PlanBuilder().build(snapshots['integration'], contract(), registered, subjects=snapshots,
                            approval_hash='a' * 64)


def test_cosmetic_title_preserves_semantics_but_criteria_change_invalidates():
    from flycode.verification.plan import contract_hash

    original = contract()
    assert contract_hash(original.model_copy(update={'title': 'New display title'})) == contract_hash(original)
    changed = original.model_copy(update={'criteria': [original.criteria[0].model_copy(
        update={'expected_case_ids': ['test_oracle.py::test_other']})]})
    assert contract_hash(changed) != contract_hash(original)
    assert contract_hash(original.model_copy(update={'version': 2})) != contract_hash(original)


def test_agent_optional_check_cannot_replace_mandatory_check(tmp_path):
    from flycode.domain.verification import CheckSpec
    from flycode.verification.plan import PlanBuilder

    snapshots = subjects(tmp_path)
    builder = PlanBuilder()
    approved = checks()
    optional = CheckSpec(id='extra', required=False, stage='target')
    plan = builder.build(snapshots['integration'], contract(), approved, subjects=snapshots,
                         approval_hash='a' * 64, proposals=[optional])
    assert plan.checks[-1].required is False
    for replacement in [approved[0].model_copy(update={'required': False}), optional.model_copy(
            update={'id': 'extra-required', 'required': True})]:
        with pytest.raises(ValueError):
            builder.build(snapshots['integration'], contract(), approved, subjects=snapshots,
                          approval_hash='a' * 64, proposals=[replacement])


def test_stage_order_cannot_make_baseline_depend_on_integrated_result(tmp_path):
    from flycode.verification.plan import PlanBuilder

    snapshots, registered = subjects(tmp_path), checks()
    registered[0] = registered[0].model_copy(update={'dependencies': ['integration']})
    registered[3] = registered[3].model_copy(update={'dependencies': []})
    with pytest.raises(ValueError, match='stage'):
        PlanBuilder().build(snapshots['integration'], contract(), registered, subjects=snapshots,
                            approval_hash='a' * 64)


def test_optional_unconfirmed_process_cleanup_blocks_global_pass():
    from flycode.domain.verification import CheckSpec
    from flycode.verification.verdict import evaluate

    registered = checks()
    attempts = [CheckAttempt(number=1, check_id=check.id, argv=['pytest'], exit_code=0, duration=1,
        cases=[CaseResult(id='test_oracle.py::test_behavior', outcome='passed')],
        collected=['test_oracle.py::test_behavior'], complete=True) for check in registered]
    registered.append(CheckSpec(id='extra', required=False))
    attempts.append(CheckAttempt(number=1, check_id='extra', argv=['pytest'], exit_code=0, duration=1,
        complete=False, state='interrupted', error_class='cleanup', reasons=['Owned process cleanup unconfirmed']))
    status, reasons, _ = evaluate(attempts, False, contract=contract(), checks=registered)
    assert status == 'inconclusive'
    assert any('cleanup' in reason.lower() for reason in reasons)
