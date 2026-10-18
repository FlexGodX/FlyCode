"""Nested aggregate criteria must be verified separately from their children."""

import json

import pytest
from test_dependency_recipe import approve_dependency_recipe, dependency_recipe_ready  # noqa: F401
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import (
    AutoClaimRequest,
    BudgetAmount,
    ClaimRequest,
    TaskCommand,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import StoreError
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def hierarchical_recipe_ready(dependency_recipe_ready, model_server):  # noqa: F811
    store, owner, host, old, root, writers, reviewers, integrator, dispatcher, source, approvals = dependency_recipe_ready
    (source / 'src/extra.py').write_text('value = 0\n')
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=old.project_id,
        task=TaskDraft.model_validate(root.model_dump(include=set(TaskDraft.model_fields))),
        budget=WorkflowBudget()), key='hierarchy-workflow')
    new_root = store.task(owner, workflow.root_task_id)
    mapping, groups = {root.id: new_root.id}, []
    def draft(task, **changes):
        return TaskDraft.model_validate({**task.model_dump(include=set(TaskDraft.model_fields)),
            'parent_id': new_root.id, 'dependencies': tuple(mapping[item] for item in task.dependencies), **changes})
    for number in range(3):
        parent = new_root.id if number < 2 else groups[1].id
        groups.append(store.create_task(owner, workflow.id, draft(root, title=f'Checked aggregate {number}',
            parent_id=parent, budget=BudgetAmount(), allowed_profile_ids=()), key=f'hierarchy-group-{number}'))
    new_writers, new_reviewers = [], []
    for number, (writer, reviewer) in enumerate(zip(writers, reviewers)):
        parent = groups[0].id if number < 2 else groups[2].id
        item = store.create_task(owner, workflow.id, draft(writer, parent_id=parent), key='hierarchy-' + writer.id)
        mapping[writer.id] = item.id
        new_writers.append(item)
        item = store.create_task(owner, workflow.id, draft(reviewer, parent_id=parent), key='hierarchy-' + reviewer.id)
        mapping[reviewer.id] = item.id
        new_reviewers.append(item)
    extra_contract = root.acceptance.model_copy(update={'id': 'hierarchy-extra'})
    fourth = store.create_task(owner, workflow.id, draft(writers[0], parent_id=groups[2].id, acceptance=extra_contract,
        title='Checked fourth output', objective='Set checked extra value to 4.',
        dependencies=(new_writers[2].id,)), key='hierarchy-fourth')
    fourth_review = store.create_task(owner, workflow.id, draft(reviewers[0], parent_id=groups[2].id,
        title='Review fourth output', acceptance=extra_contract), key='hierarchy-fourth-review')
    new_writers.append(fourth)
    new_reviewers.append(fourth_review)
    new_integrator = store.create_task(owner, workflow.id, draft(integrator,
        dependencies=tuple(item.id for item in new_reviewers),
        budget=BudgetAmount(tool_calls=73)), key='hierarchy-integrator')
    mapping[integrator.id] = new_integrator.id
    new_approvals = {mapping[identity]: approval for identity, approval in approvals.items()}
    new_approvals.update({task.id: approvals[root.id] for task in groups})
    oracle = source.parent / 'hierarchy-extra-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n    assert runpy.run_path("src/extra.py")["value"] == 4\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    approval = engine.oracles.approve(extra_contract, engine.oracles.read(approvals[root.id]).checks, oracle.parent)
    new_approvals.update({fourth.id: approval, fourth_review.id: approval})
    for writer in new_writers:
        dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision,
            key='hierarchy-bind-' + writer.id, brain='heuristic_fsm')
    previous_response = model_server.respond
    def response(request):
        if 'Set checked extra value to 4.' not in str(request['messages']):
            return previous_response(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/extra.py', 'old_text': 'value = 0', 'new_text': 'value = 4'}}]}))
    model_server.respond = response
    return (store, owner, host, workflow, new_root, new_writers, new_reviewers, new_integrator,
        dispatcher, source, new_approvals), groups


def run_hierarchy(prepared):
    store, owner, host, workflow, _, _, _, _, dispatcher, _, _ = prepared
    try:
        for number in range(36):
            dispatcher.tick(host, key=f'hierarchy-tick-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None and handle.process_stopped.is_set()
            if store.workflow(owner, workflow.id).state == 'succeeded':
                break
        return store.workflow(owner, workflow.id)
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_four_writers_in_nested_groups_complete_only_after_each_aggregate_check(hierarchical_recipe_ready, monkeypatch):
    prepared, groups = hierarchical_recipe_ready
    store, owner, _, workflow, _, writers, _, _, dispatcher, source, _ = prepared
    before = store.budget(owner, workflow.id)
    recipe = approve_dependency_recipe(prepared)
    assert len(recipe.branches) == 4 and len(recipe.tasks) == 13
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Hierarchy used a manual claim'))
    monkeypatch.setattr(store, 'command', lambda *a, **kw: pytest.fail('Hierarchy required an owner command'))
    assert run_hierarchy(prepared).state == 'succeeded'
    attempts = store.attempts(owner, workflow.id)
    assert len(attempts) == 9
    assert not {task.id for task in groups} & {attempt.task_id for attempt in attempts}
    record = Integration(store, dispatcher.root).get(owner,
        store.db.execute("SELECT id FROM integrations WHERE state='completed'").fetchone()['id'])
    assert set(record.verdicts) == set(record.applied_verdicts) == {workflow.root_task_id,
        prepared[7].id, *(task.id for task in groups), *(task.id for task in writers)}
    assert all(store.task(owner, task.id).state == 'succeeded' for task in groups)
    assert Integration(store, dispatcher.root).refresh(owner, record.id).state == 'completed'
    assert (source / 'src/parser.py').read_text() == 'value = 3\n'
    assert (source / 'src/extra.py').read_text() == 'value = 4\n'
    assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    ledger = store.budget(owner, workflow.id)
    assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain
    monkeypatch.undo()
    current = store.task(owner, groups[1].id)
    changed = store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
        idempotency_key='changed-aggregate-criterion', changes={'objective': 'A new owner acceptance requirement.'}))
    assert changed.semantic_revision > current.semantic_revision
    assert Integration(store, dispatcher.root).refresh(owner, record.id).state == 'invalidated'


@pytest.mark.parametrize('change', ['missing-approval', 'executable-group', 'leaf-impostor'])
def test_unapproved_or_executable_aggregate_never_enqueues(hierarchical_recipe_ready, change):
    prepared, groups = hierarchical_recipe_ready
    store, owner, _, workflow, _, writers, _, _, _, _, approvals = prepared
    if change == 'missing-approval':
        approvals.pop(groups[1].id)
    elif change == 'executable-group':
        task = groups[1]
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='executable-aggregate', changes={'allowed_profile_ids': writers[0].allowed_profile_ids,
                'budget': {'tool_calls': 1}}))
    else:
        task = store.create_task(owner, workflow.id, TaskDraft(title='Unbound leaf impostor',
            parent_id=groups[1].id, objective='Do not silently mark this done.',
            acceptance=groups[1].acceptance, permissions=groups[1].permissions, budget=BudgetAmount()), key='leaf-impostor')
        approvals[task.id] = approvals[groups[1].id]
    with pytest.raises(StoreError):
        approve_dependency_recipe(prepared)
    assert not store.db.execute('SELECT 1 FROM pipelines WHERE workflow_id=?', (workflow.id,)).fetchone()
    assert all(task.state == 'planned' for task in store.tasks(owner, workflow.id))
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id).spent == BudgetAmount()


@pytest.mark.parametrize('operation', ['set_metadata', 'revise'])
def test_owner_can_change_aggregate_metadata_and_invalidate_its_contract(hierarchical_recipe_ready, operation):
    prepared, groups = hierarchical_recipe_ready
    store, owner, host, workflow, _, _, _, _, dispatcher, _, _ = prepared
    recipe = approve_dependency_recipe(prepared)
    current = store.task(owner, groups[1].id)
    changed = store.command(owner, current.id, TaskCommand(operation=operation, expected_revision=current.revision,
        idempotency_key='change-approved-aggregate', changes={'title': 'Renamed aggregate'} if operation == 'set_metadata'
            else {'objective': 'Another required aggregate outcome.'}))
    assert changed.revision == current.revision + 1
    service = Pipelines(store, dispatcher.root)
    assert service.get(owner, recipe.id) == recipe
    result = service.advance(host, workflow.id)
    assert ('pipeline_changed' in result) if operation == 'revise' else result == ()
    assert store.attempts(owner, workflow.id) == []


def test_paused_ancestor_preserves_other_group_progress_and_fences_all_claims(hierarchical_recipe_ready):
    prepared, groups = hierarchical_recipe_ready
    store, owner, host, workflow, _, writers, _, _, _dispatcher, source, _ = prepared
    approve_dependency_recipe(prepared)
    current = store.task(owner, groups[1].id)
    store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
        idempotency_key='pause-outer-aggregate'))
    run_hierarchy(prepared)
    assert all(store.task(owner, task.id).completion_id is not None for task in writers[:2])
    assert not any(attempt.task_id in {task.id for task in writers[2:]} for attempt in store.attempts(owner, workflow.id))
    current = store.task(owner, writers[2].id)
    current = store.command(owner, current.id, TaskCommand(operation='enqueue', expected_revision=current.revision,
        idempotency_key='explicit-ready-under-paused-group'))
    instance = next(item for item in store.instances(host) if item.profile_id in current.allowed_profile_ids)
    before = store.budget(owner, workflow.id)
    direct = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=current.id, expected_task_revision=current.revision, reserve=current.budget, idempotency_key='paused-parent-direct'))
    automatic = store.claim_next(host, AutoClaimRequest(instance_id=instance.id,
        expected_instance_revision=instance.revision, idempotency_key='paused-parent-auto'))
    assert direct.attempt is automatic.attempt is None
    assert 'pipeline_branch_paused' in direct.reasons and 'pipeline_branch_paused' in automatic.reasons
    assert store.budget(owner, workflow.id) == before
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def test_child_success_does_not_satisfy_a_failed_aggregate_criterion(hierarchical_recipe_ready):
    prepared, groups = hierarchical_recipe_ready
    store, owner, _, _workflow, root, writers, _, _, dispatcher, source, approvals = prepared
    group = groups[1]
    contract = group.acceptance.model_copy(update={'id': 'hierarchy-group-failure'})
    store.command(owner, group.id, TaskCommand(operation='revise', expected_revision=group.revision,
        idempotency_key='require-more-than-child-success', changes={'acceptance': contract.model_dump(mode='json')}))
    oracle = source.parent / 'hierarchy-group-failure-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n    assert runpy.run_path("src/parser.py")["value"] >= 9\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    approvals[group.id] = engine.oracles.approve(contract, engine.oracles.read(approvals[root.id]).checks, oracle.parent)
    approve_dependency_recipe(prepared)
    assert run_hierarchy(prepared).state != 'succeeded'
    assert all(store.task(owner, task.id).state == 'succeeded' for task in writers)
    assert all(store.task(owner, task.id).state != 'succeeded' for task in groups)
    record = Integration(store, dispatcher.root).get(owner,
        store.db.execute('SELECT id FROM integrations').fetchone()['id'])
    assert record.state == 'failed' and record.error == 'verification_failed'
    assert record.verdicts[group.id].status != 'passed'
    assert not record.applied_verdicts
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    assert (source / 'src/extra.py').read_text() == 'value = 0\n'
    assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
