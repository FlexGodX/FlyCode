"""Owner recipe publication pins exact registered profiles after physical checks."""

import json

import pytest
from test_tracker_results import (  # noqa: F401
    approve_parent,
    live_parent,
    managed_pipeline,
    parent_ready,
    pipeline_ready,
    wait_handles,
)

from flycode.domain.orchestration import TaskDraft
from flycode.domain.verification_control import VerificationProfileDraft
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import Store, StoreError
from flycode.verification.oracle import OracleRegistry
from flycode.verification.registry import VerificationRegistry


@pytest.fixture
def pinned_parent(live_parent):  # noqa: F811
    p = live_parent
    registry = VerificationRegistry(p['store'])
    runtime = p['dispatcher'].root / 'verification/oracles'
    profiles = {}
    for task in p['store'].tasks(p['owner'], p['workflow'].id):
        approval = p['approvals'][task.id]
        if approval not in profiles:
            saved = OracleRegistry(runtime).read(approval)
            profiles[approval] = registry.register_profile(p['owner'], task.project_id,
                VerificationProfileDraft(title='Owner registered criteria', acceptance=saved.contract, checks=saved.checks),
                oracle_root=runtime / approval / 'files')
            assert profiles[approval].approval_hash == approval
    p['profile_pins'] = {task_id: profiles[approval] for task_id, approval in p['approvals'].items()}
    return p


def state(p):
    store, owner, workflow = p['store'], p['owner'], p['workflow']
    return (store.workflow(owner, workflow.id), store.tasks(owner, workflow.id), store.attempts(owner, workflow.id),
        store.budget(owner, workflow.id), store.outbox_count(), store._counter('task_number'),
        tuple(map(tuple, store.db.execute('SELECT * FROM pipelines'))),
        tuple(map(tuple, store.db.execute('SELECT * FROM worker_bindings ORDER BY id'))),
        tuple(map(tuple, store.db.execute('SELECT * FROM pipeline_receipts ORDER BY pipeline_id,stage'))))


def alter_profile(p, profile, kind):
    connection = Store(p['store'].path)
    try:
        if kind == 'row':
            altered = profile.model_copy(update={'draft': profile.draft.model_copy(update={'title': 'Changed after preflight'})})
            connection.db.execute('UPDATE verification_profiles SET data=? WHERE id=?', (altered.model_dump_json(), profile.id))
        elif kind == 'project':
            from flycode.orchestration.projects import Projects

            current = Projects(connection).get(profile.project_id)
            altered = current.model_copy(update={'revision': current.revision + 1})
            connection.db.execute('UPDATE canonical_projects SET data=? WHERE id=?', (altered.model_dump_json(), current.id))
        else:
            runtime = p['dispatcher'].root / 'verification/oracles'
            draft = profile.draft.model_copy(update={'acceptance': profile.draft.acceptance.model_copy(update={'version': 2})})
            VerificationRegistry(connection).register_profile(p['owner'], profile.project_id, draft,
                oracle_root=runtime / profile.approval_hash / 'files')
    finally:
        connection.close()


@pytest.mark.parametrize('kind', ['row', 'head', 'project'])
def test_approval_profile_change_after_registered_preflight_publishes_nothing(pinned_parent, monkeypatch, kind):
    p = pinned_parent
    original, calls = VerificationRegistry.profile, []

    def changed(registry, scope, identity):
        result = original(registry, scope, identity)
        if registry.store is p['store']:
            calls.append(identity)
            if len(calls) == len(p['approvals']):
                alter_profile(p, p['profile_pins'][p['child'].id], kind)
        return result

    monkeypatch.setattr(VerificationRegistry, 'profile', changed)
    before = state(p)
    with pytest.raises(StoreError, match='verification_profile_changed|project_changed'):
        approve_parent(p, profile_pins=p['profile_pins'])
    assert len(calls) == len(p['approvals'])
    assert state(p) == before
    assert (p['source'] / 'src/parser.py').read_bytes() == b'value = 0\n'


def suspended(p):
    recipe = approve_parent(p, profile_pins=p['profile_pins'])
    result = p['dispatcher'].tick(p['host'], key='prepare-pinned-parent')
    assert result.attempt is not None and result.attempt.task_id == p['parent'].id
    wait_handles(p['dispatcher'])
    assert Pipelines(p['store'], p['dispatcher'].root).advance(p['host'], recipe.workflow_id) == ()
    assert p['store'].task(p['owner'], p['parent'].id).blocked_reason == 'waiting_children'
    return recipe


def replan_arguments(p, recipe, **values):
    store, owner = p['store'], p['owner']
    return {'changes': {p['child'].id: {'objective': 'Repair the parser under the same exact owner criteria.'}},
        'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)},
        'profile_pins': p['profile_pins'], 'key': 'replan-pinned-child', **values}


@pytest.mark.parametrize('kind', ['row', 'head', 'project'])
def test_replan_profile_change_after_registered_preflight_publishes_nothing(pinned_parent, monkeypatch, kind):
    p = pinned_parent
    recipe = suspended(p)
    original, calls = VerificationRegistry.profile, []

    def changed(registry, scope, identity):
        result = original(registry, scope, identity)
        if registry.store is p['store']:
            calls.append(identity)
            if len(calls) == len(recipe.approval_hashes):
                alter_profile(p, p['profile_pins'][p['child'].id], kind)
        return result

    monkeypatch.setattr(VerificationRegistry, 'profile', changed)
    before = state(p)
    with pytest.raises(StoreError, match='verification_profile_changed|project_changed'):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id, **replan_arguments(p, recipe))
    assert len(calls) == len(recipe.approval_hashes)
    assert state(p) == before


def test_exact_profile_pins_approve_and_replan_without_resetting_parent_spending(pinned_parent, monkeypatch):
    p = pinned_parent
    original, checked = VerificationRegistry._profile_sql, []

    def SQL_only(registry, profile):
        assert registry.store.db.in_transaction
        checked.append(profile.id)
        return original(registry, profile)

    monkeypatch.setattr(VerificationRegistry, '_profile_sql', SQL_only)
    recipe = suspended(p)
    store, owner = p['store'], p['owner']
    before = store.budget(owner, recipe.workflow_id)
    deadline = store.workflow(owner, recipe.workflow_id).deadline_at
    arguments = replan_arguments(p, recipe)
    service = Pipelines(store, p['dispatcher'].root)
    revised = service.replan(owner, recipe.id, **arguments)
    assert revised.graph_revision == 1 and revised.id == recipe.id
    assert store.budget(owner, recipe.workflow_id) == before
    assert store.workflow(owner, recipe.workflow_id).deadline_at == deadline
    assert len(store.attempts(owner, recipe.workflow_id)) == 1
    assert checked and set(checked) == {profile.id for profile in p['profile_pins'].values()}
    after = state(p)
    assert service.replan(owner, recipe.id, **arguments) == revised
    assert state(p) == after
    changed = dict(arguments, profile_pins={**arguments['profile_pins'], p['child'].id: arguments['profile_pins'][p['parent'].id]})
    with pytest.raises(StoreError, match='idempotency'):
        service.replan(owner, recipe.id, **changed)
    assert state(p) == after


@pytest.mark.parametrize('kind', ['missing', 'criteria', 'reruns', 'untrusted'])
def test_profile_refs_cannot_reduce_or_replace_registered_requirements(pinned_parent, kind):
    p = pinned_parent
    pins = dict(p['profile_pins'])
    if kind == 'missing':
        del pins[p['child'].id]
    else:
        profile = pins[p['child'].id]
        changes = {'criteria': {'acceptance': profile.draft.acceptance.model_copy(update={'id': 'different-goal'})},
            'reruns': {'reruns': 1}, 'untrusted': {'trust': 'untrusted'}}[kind]
        runtime = p['dispatcher'].root / 'verification/oracles'
        pins[p['child'].id] = VerificationRegistry(p['store']).register_profile(p['owner'], profile.project_id,
            profile.draft.model_copy(update=changes), oracle_root=runtime / profile.approval_hash / 'files')
    before = state(p)
    with pytest.raises(StoreError):
        approve_parent(p, profile_pins=pins)
    assert state(p) == before


def test_new_branch_pins_are_checked_before_server_id_allocation(pinned_parent):
    p = pinned_parent
    recipe = suspended(p)
    store, owner = p['store'], p['owner']
    writer = TaskDraft.model_validate({**p['child'].model_dump(include=set(TaskDraft.model_fields)),
        'title': 'Additional owner branch', 'objective': 'Verify the same repair in another explicit branch.',
        'dependencies': (p['child'].id,)})
    reviewer = TaskDraft.model_validate(p['child_review'].model_dump(include=set(TaskDraft.model_fields)))
    addition = {'writer': writer, 'reviewer': reviewer, 'template_writer_id': p['child'].id,
        'approval_hash': p['approvals'][p['child'].id]}
    arguments = replan_arguments(p, recipe, changes={}, new_branches=[addition])
    before = state(p)
    service = Pipelines(store, p['dispatcher'].root)
    with pytest.raises(StoreError):
        service.replan(owner, recipe.id, **arguments)
    assert state(p) == before
    arguments['profile_pins'] = {**p['profile_pins'], 'new:0': p['profile_pins'][p['child'].id]}
    revised = service.replan(owner, recipe.id, **arguments)
    added = set(revised.tasks) - set(recipe.tasks)
    assert len(added) == 2 and len(revised.branches) == 3
    assert all(revised.approval_hashes[identity] == p['profile_pins'][p['child'].id].approval_hash for identity in added)
    assert store.budget(owner, recipe.workflow_id) == before[3]


def test_exact_approval_replay_keeps_its_saved_receipt_after_profile_supersession(pinned_parent):
    p = pinned_parent
    revisions = {task.id: task.revision for task in p['store'].tasks(p['owner'], p['workflow'].id)}
    arguments = {'expected_revisions': revisions, 'profile_pins': p['profile_pins']}
    recipe = approve_parent(p, **arguments)
    alter_profile(p, p['profile_pins'][p['child'].id], 'head')
    before = state(p)
    assert approve_parent(p, **arguments) == recipe
    assert state(p) == before
    assert not p['store'].attempts(p['owner'], recipe.workflow_id)


def test_pinned_owner_replan_reaches_fresh_review_and_current_integration(pinned_parent, model_server):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.integration import Integration

    p = pinned_parent
    recipe = suspended(p)
    store, owner, dispatcher = p['store'], p['owner'], p['dispatcher']
    deadline = store.workflow(owner, recipe.workflow_id).deadline_at
    arguments = replan_arguments(p, recipe)
    revised = Pipelines(store, dispatcher.root).replan(owner, recipe.id, **arguments)
    original, calls = model_server.respond, []

    def respond(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return original(request)
        assert revised.tasks[p['child'].id].objective in messages
        calls.append(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))

    model_server.respond = respond
    for number in range(18):
        dispatcher.tick(p['host'], key=f'pinned-owner-delivery-{number}')
        wait_handles(dispatcher)
        assert store.workflow(owner, recipe.workflow_id).deadline_at == deadline
        if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
            break
    assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
    assert len(calls) == 1 and len(store.attempts(owner, recipe.workflow_id)) == 6
    result = store.task(owner, p['parent'].id).result_id
    assert result is not None and Integration(store, dispatcher.root).current(owner, result)
    assert store.task(owner, p['child'].id).completion_id is not None
    assert (p['source'] / 'src/parser.py').read_bytes() == b'value = 1\n'
    assert (p['source'] / 'src/extra.py').read_bytes() == b'value = 4\n'
    assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    ledger = store.budget(owner, recipe.workflow_id)
    assert ledger.spent.model_calls == 3 and ledger.reserved == BudgetAmount() and not ledger.uncertain
