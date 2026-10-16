"""Canonical ownership is committed once, even across independent connections."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion


@pytest.fixture
def setup(tmp_path, model_server):
    from flycode.domain.orchestration import (
        AccessScope,
        AgentProfileDraft,
        ExecutionPermission,
    )
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.orchestration.store import Store

    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.discover()[0]
    catalogue.probe(model.model_ref, 'primary')
    now = [1000.]
    owner = AccessScope(actor_id='user', project_ids={'p', 'q'}, role='owner', global_queue_control=True)
    runtime = AccessScope(actor_id='host', project_ids={'p', 'q'}, role='runtime')
    store = Store(tmp_path / 'queue.sqlite', catalogue=catalogue, clock=lambda: now[0])
    permission = ExecutionPermission(approved=True, tools=('read_file', 'edit_file', 'run_tests'),
                                     read_scopes=('.',), write_scopes=('src',))
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Repair only the approved files.', capabilities=('python',),
        project_ids=('p',), permissions=permission, context_limit=4096))
    instance = store.register_instance(runtime, profile.id)
    yield store, owner, runtime, profile, instance, now, catalogue, permission
    store.close()


def draft(permission, **changes):
    from flycode.domain.orchestration import BudgetAmount, TaskDraft

    values = {'title': 'Repair parser', 'objective': 'Fix the parser without regressions.',
        'acceptance': AcceptanceContract(id='parser-v1', version=1, criteria=[AcceptanceCriterion(
            id='repair', check_ids=['regression'], expected_case_ids=['tests/test_parser.py::test_parse'])]),
        'permissions': permission, 'budget': BudgetAmount(model_calls=10, tool_calls=20), 'capabilities': ('python',)}
    return TaskDraft.model_validate({**values, **changes})


def workflow(setup, **changes):
    from flycode.domain.orchestration import WorkflowBudget, WorkflowDraft

    store, owner, _, _, _, _, _, permission = setup
    return store.create_workflow(owner, WorkflowDraft(project_id='p', task=draft(permission, **changes),
                                 budget=WorkflowBudget()), key='create-' + str(len(store.workflows(owner))))


def command(store, owner, task, operation, **changes):
    from flycode.domain.orchestration import TaskCommand

    return store.command(owner, task.id, TaskCommand(operation=operation, expected_revision=task.revision,
        idempotency_key=f'{task.id}-{task.revision}-{operation}', changes=changes))


def queued(setup, **changes):
    store, owner, *_ = setup
    wf = workflow(setup, **changes)
    root = store.task(owner, wf.root_task_id)
    root = command(store, owner, root, 'enqueue')
    store.start_next(owner, key='start-' + wf.id)
    return store.workflow(owner, wf.id), root


def request(instance, task=None, **changes):
    from flycode.domain.orchestration import BudgetAmount, ClaimRequest

    return ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id if task else None, expected_task_revision=task.revision if task else None,
        reserve=BudgetAmount(model_calls=2, tool_calls=4), idempotency_key=changes.pop('key', 'claim'), **changes)


def test_draft_is_inert_idempotent_and_project_scoped(setup, monkeypatch):
    from flycode.domain.orchestration import AccessScope, TaskDraft, WorkflowDraft
    from flycode.llm import make_lmstudio_cortex
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    calls = []
    monkeypatch.setattr('flycode.llm.make_lmstudio_cortex', lambda *a, **k: calls.append(make_lmstudio_cortex))
    payload = WorkflowDraft(project_id='p', task=TaskDraft(title='Incomplete draft'))
    first = store.create_workflow(owner, payload, key='draft')
    assert store.create_workflow(owner, payload, key='draft') == first
    assert not calls and not store.attempts(owner, first.id)
    assert store.task(owner, first.root_task_id).state == 'planned'
    foreign = AccessScope(actor_id='user', project_ids={'q'}, role='owner')
    with pytest.raises(StoreError, match='scope'):
        store.create_workflow(foreign, payload, key='draft')
    with pytest.raises(StoreError, match='idempotency'):
        store.create_workflow(owner, payload.model_copy(update={'task': TaskDraft(title='Other')}), key='draft')


@pytest.mark.parametrize('missing', ['objective', 'acceptance', 'permissions', 'budget'])
def test_enqueue_requires_complete_owner_contract(setup, missing):
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = workflow(setup, **{missing: '' if missing == 'objective' else None})
    task = store.task(owner, wf.root_task_id)
    with pytest.raises(StoreError, match='incomplete'):
        command(store, owner, task, 'enqueue')
    assert store.task(owner, task.id).state == 'planned'
    assert not store.attempts(owner, wf.id)


def test_metadata_revision_does_not_change_semantic_contract_and_replays_before_revision(setup):
    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = workflow(setup)
    original = store.task(owner, wf.root_task_id)
    update = TaskCommand(operation='set_metadata', expected_revision=original.revision,
        idempotency_key='metadata', changes={'title': 'New title', 'tags': ['parser']})
    changed = store.command(owner, original.id, update)
    assert changed.semantic_revision == original.semantic_revision
    assert changed.contract_hash == original.contract_hash and changed.revision == original.revision + 1
    assert store.command(owner, original.id, update) == changed
    with pytest.raises(StoreError, match='revision') as error:
        store.command(owner, original.id, update.model_copy(update={'idempotency_key': 'stale'}))
    assert error.value.current['revision'] == changed.revision


def test_dependency_cycles_foreign_scope_and_parent_limits_are_rejected(setup):
    from flycode.domain.orchestration import WorkflowBudget, WorkflowDraft
    from flycode.orchestration.store import StoreError

    store, owner, _, _, _, _, _, permission = setup
    wf = workflow(setup)
    a = store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id), key='a')
    b = store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id, dependencies=(a.id,)), key='b')
    with pytest.raises(StoreError, match='cycle'):
        command(store, owner, a, 'revise', dependencies=[b.id])
    foreign = store.create_workflow(owner, WorkflowDraft(project_id='q', task=draft(permission),
                                    budget=WorkflowBudget()), key='foreign')
    with pytest.raises(StoreError, match='dependency'):
        store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id,
                          dependencies=(foreign.root_task_id,)), key='bad')
    for i in range(6):
        store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id), key=f'child{i}')
    with pytest.raises(StoreError, match='children'):
        store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id), key='ninth')


def test_two_connections_claim_once_with_atomic_reservation_and_event(setup):
    from flycode.orchestration.store import Store, StoreError

    store, owner, runtime, profile, first, now, catalogue, _ = setup
    wf, task = queued(setup)
    second = store.register_instance(runtime, profile.id)
    barrier = Barrier(2)

    def compete(instance):
        connection = Store(store.path, catalogue=catalogue, clock=lambda: now[0])
        try:
            barrier.wait()
            return connection.claim(runtime, request(instance, task, key='race-' + instance.id))
        except StoreError as exc:
            return exc.code
        finally:
            connection.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(compete, [first, second]))
    wins = [r for r in results if not isinstance(r, str) and r.attempt is not None]
    assert len(wins) == 1
    assert wins[0].attempt.workflow_revision == wf.revision
    assert wins[0].attempt.workflow_semantic_revision == wf.semantic_revision
    assert len(store.attempts(owner, wf.id)) == 1
    assert store.budget(owner, wf.id).reserved.model_calls == 2
    assigned = [event for event in store.events(owner, wf.id) if event.kind == 'attempt_assigned']
    assert len(assigned) == 1 and assigned[0].attempt_id == wins[0].attempt.id
    assert store.outbox_count() == len(store.events(owner, wf.id))
    assert store.workflow(owner, wf.id).dispatch_count == 0


def test_restart_keeps_ownership_and_replay_without_second_writer(setup):
    from flycode.orchestration.store import Store

    store, owner, runtime, profile, instance, now, catalogue, _ = setup
    wf, task = queued(setup)
    claim = request(instance, task)
    first = store.claim(runtime, claim)
    restarted = Store(store.path, catalogue=catalogue, clock=lambda: now[0])
    try:
        assert restarted.claim(runtime, claim) == first
        now[0] += 31
        restarted.recover(runtime)
        assert restarted.attempts(owner, wf.id)[0].state == 'reconciling'
        other = restarted.register_instance(runtime, profile.id)
        assert restarted.claim(runtime, request(other, key='other')).attempt is None
        assert restarted.budget(owner, wf.id).reserved.model_calls == 2
        assert restarted.workflow(owner, wf.id).dispatch_count == 0
    finally:
        restarted.close()


def test_spend_cancel_stop_and_retry_never_refund_spent_or_release_unknown_writer(setup):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.store import StoreError

    store, owner, runtime, profile, instance, _, _, _ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1, tool_calls=2), key='model-and-tool')
    task = command(store, owner, store.task(owner, task.id), 'release_assignment')
    with pytest.raises(StoreError, match='fence'):
        store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key='late-spend')
    other = store.register_instance(runtime, profile.id)
    assert store.claim(runtime, request(other, key='before-stop')).attempt is None
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait')
    task = command(store, owner, store.task(owner, task.id), 'retry')
    assert task.state == 'ready'
    ledger = store.budget(owner, wf.id)
    assert ledger.spent.model_calls == 1 and ledger.reserved.model_calls == 0
    second = store.claim(runtime, request(other, task, key='retry')).attempt
    assert second.id != attempt.id and second.fence > attempt.fence
    assert store.budget(owner, wf.id).spent == ledger.spent


def test_pause_dependency_profile_and_verification_reserve_gate_claims(setup):
    from flycode.domain.orchestration import (
        BudgetAmount,
        ClaimRequest,
        WorkflowBudget,
        WorkflowDraft,
    )

    store, owner, runtime, _, instance, _, _, permission = setup
    small = BudgetAmount(model_calls=5, tool_calls=10)
    wf = store.create_workflow(owner, WorkflowDraft(project_id='p', task=draft(permission, budget=small),
        budget=WorkflowBudget(max_model_calls=5, max_tool_calls=10)), key='small')
    root = command(store, owner, store.task(owner, wf.root_task_id), 'enqueue')
    store.start_next(owner, key='small-start')
    oversized = ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        reserve=BudgetAmount(model_calls=5, tool_calls=8), idempotency_key='reserve')
    blocked = store.claim(runtime, oversized)
    assert blocked.attempt is None and 'verification_reserve' in blocked.reasons
    root = command(store, owner, root, 'pause')
    assert 'paused' in store.claim(runtime, request(instance, key='paused')).reasons
    root = command(store, owner, root, 'resume')
    prerequisite = store.create_task(owner, wf.id, draft(permission, parent_id=root.id, budget=small), key='prerequisite')
    child = store.create_task(owner, wf.id, draft(permission, parent_id=root.id, budget=small,
        dependencies=(prerequisite.id,)), key='dependent')
    child = command(store, owner, child, 'enqueue')
    assert 'dependency' in store.claim(runtime, request(instance, child, key='dependency')).reasons


def test_root_wip_and_queue_pause_are_persistent(setup):
    store, owner, runtime, _, instance, *_ = setup
    first, _ = queued(setup)
    other = workflow(setup)
    command(store, owner, store.task(owner, other.root_task_id), 'enqueue')
    assert store.start_next(owner, key='second') is None
    store.pause_queue(owner, True, key='queue-pause')
    assert 'queue_paused' in store.claim(runtime, request(instance)).reasons
    assert store.workflow(owner, first.id).state == 'running'
    assert store.workflow(owner, other.id).state == 'draft'


def test_outbox_failure_rolls_back_assignment_ledger_and_idempotency(setup):
    import sqlite3

    store, owner, runtime, _, instance, *_ = setup
    wf, task = queued(setup)
    before = store.events(owner, wf.id)
    store.db.execute("CREATE TEMP TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'outbox failure'); END")
    claim = request(instance, task)
    with pytest.raises(sqlite3.IntegrityError, match='outbox failure'):
        store.claim(runtime, claim)
    assert not store.attempts(owner, wf.id)
    assert store.budget(owner, wf.id).reserved.model_calls == 0
    assert store.task(owner, task.id) == task
    assert store.events(owner, wf.id) == before
    store.db.execute('DROP TRIGGER fail_outbox')
    assert store.claim(runtime, claim).attempt is not None


def test_actual_starts_drive_root_aging_and_failed_spawn_does_not_advance_it(setup):
    from flycode.domain.orchestration import BudgetAmount

    store, owner, runtime, profile, _, now, _, _ = setup
    oldest = workflow(setup, priority=0)
    command(store, owner, store.task(owner, oldest.root_task_id), 'enqueue')
    for i in range(9):
        now[0] += 1
        wf = workflow(setup, priority=3)
        command(store, owner, store.task(owner, wf.root_task_id), 'enqueue')
    for i in range(7):
        wf = store.start_next(owner, key=f'aging-start-{i}')
        assert wf.id != oldest.id and store._counter('root_starts') == i
        instance = store.register_instance(runtime, profile.id)
        attempt = store.claim(runtime, request(instance, key=f'aging-claim-{i}')).attempt
        store.confirm_started(runtime, attempt.id, attempt.fence)
        store.confirm_started(runtime, attempt.id, attempt.fence)
        assert store._counter('root_starts') == i + 1
        store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key=f'aging-model-{i}')
        command(store, owner, store.task(owner, wf.root_task_id), 'cancel')
        store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='cancelled')
    selected = store.start_next(owner, key='eighth')
    assert selected.id == oldest.id
    instance = store.register_instance(runtime, profile.id)
    unstarted = store.claim(runtime, request(instance, key='failed-spawn')).attempt
    assert store.workflow(owner, selected.id).started_at is None
    store.confirm_stopped(runtime, unstarted.id, unstarted.fence, outcome='retry_wait')
    assert store._counter('root_starts') == 7


def test_four_active_instances_and_disjoint_scope_admission(setup):
    from flycode.domain.orchestration import ExecutionPermission

    store, owner, runtime, profile, _, _, _, permission = setup
    wf = workflow(setup)
    command(store, owner, store.task(owner, wf.root_task_id), 'enqueue')
    tasks = []
    for i in range(5):
        scoped = ExecutionPermission(approved=True, tools=permission.tools, read_scopes=('.',),
                                     write_scopes=(f'src/module{i}',))
        task = store.create_task(owner, wf.id, draft(scoped, parent_id=wf.root_task_id), key=f'parallel{i}')
        tasks.append(command(store, owner, task, 'enqueue'))
    store.start_next(owner, key='parallel-start')
    for i, task in enumerate(tasks):
        instance = store.register_instance(runtime, profile.id)
        result = store.claim(runtime, request(instance, task, key=f'parallel-claim{i}'))
        if i < 4:
            assert result.attempt is not None
        else:
            assert result.attempt is None and 'instance_limit' in result.reasons
    assert store.budget(owner, wf.id).reserved.model_calls == 8


def test_profile_mismatch_and_changed_model_revision_never_dispatch(setup, model_server):
    store, owner, runtime, profile, instance, _, catalogue, _ = setup
    wf, task = queued(setup, allowed_profile_ids=('another-profile',))
    blocked = store.claim(runtime, request(instance, task, key='manual-profile'))
    assert blocked.attempt is None and 'profile_scope' in blocked.reasons
    task = command(store, owner, task, 'assign_profile', allowed_profile_ids=[profile.id])
    catalogue.probe(profile.model_ref, 'primary')
    calls = len(model_server.requests)
    blocked = store.claim(runtime, request(instance, task, key='stale-model'))
    assert blocked.attempt is None and 'model_unavailable' in blocked.reasons
    assert len(model_server.requests) == calls and not store.attempts(owner, wf.id)


def test_budget_spend_is_idempotent_before_stale_fence_check(setup):
    from flycode.domain.orchestration import BudgetAmount

    store, owner, runtime, _, instance, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    amount = BudgetAmount(model_calls=1)
    first = store.spend(runtime, attempt.id, attempt.fence, amount, key='http-request-1')
    command(store, owner, store.task(owner, task.id), 'release_assignment')
    assert store.spend(runtime, attempt.id, attempt.fence, amount, key='http-request-1') == first
    assert store.budget(owner, wf.id).spent.model_calls == 1


def test_parent_completion_edges_cannot_deadlock_descendants(setup):
    from flycode.orchestration.store import StoreError

    store, owner, _, _, _, _, _, permission = setup
    wf = workflow(setup)
    with pytest.raises(StoreError, match='cycle'):
        store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id,
            dependencies=(wf.root_task_id,)), key='parent-dependency')


def test_pause_resume_after_deadline_is_incomplete_and_spending_survives(setup):
    from flycode.domain.orchestration import BudgetAmount

    store, owner, runtime, _, instance, now, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key='before-pause-model')
    task = command(store, owner, store.task(owner, task.id), 'pause')
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait')
    now[0] += 1801
    command(store, owner, store.task(owner, task.id), 'resume')
    assert store.workflow(owner, wf.id).state == 'incomplete'
    assert not store.workflow(owner, wf.id).active_slot
    assert store.budget(owner, wf.id).spent.model_calls == 1


def test_retry_limit_and_uncertain_spend_need_explicit_reconciliation(setup):
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _profile, instance, *_ = setup
    wf, task = queued(setup)
    for i in range(3):
        instance = store.instance(runtime, instance.id)
        attempt = store.claim(runtime, request(instance, task, key=f'limited-{i}')).attempt
        assert attempt is not None
        store.confirm_started(runtime, attempt.id, attempt.fence)
        if i == 0:
            store.recover(runtime)
            with pytest.raises(StoreError, match='uncertain'):
                store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed')
        store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
        task = store.task(owner, task.id)
        if i < 2:
            task = command(store, owner, task, 'retry')
    with pytest.raises(StoreError, match='attempt_limit'):
        command(store, owner, task, 'retry')
    assert len(store.attempts(owner, wf.id)) == 3


def test_global_pause_requires_global_authority(setup):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    store, *_ = setup
    limited = AccessScope(actor_id='limited', project_ids={'p'}, role='owner')
    with pytest.raises(StoreError, match='scope'):
        store.pause_queue(limited, True, key='cross-project-stop')


def test_model_discovery_alone_and_file_scope_mismatch_are_ineligible(setup, model_server):
    from flycode.domain.orchestration import ExecutionPermission
    from flycode.llm.catalogue import ModelCatalogue

    store, owner, runtime, _, instance, _, _, permission = setup
    outside = ExecutionPermission(approved=True, tools=permission.tools, read_scopes=('.',), write_scopes=('other',))
    _, task = queued(setup, permissions=outside)
    assert 'profile_permission' in store.claim(runtime, request(instance, task, key='outside')).reasons
    task = command(store, owner, task, 'revise', permissions=permission.model_dump(mode='json'))
    fresh = ModelCatalogue(model_server.url)
    fresh.discover()
    store.catalogue = fresh
    calls = len(model_server.requests)
    result = store.claim(runtime, request(instance, task, key='discovered'))
    assert result.attempt is None and 'model_unavailable' in result.reasons
    assert len(model_server.requests) == calls


def test_state_injection_and_foreign_event_reads_are_rejected(setup):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = workflow(setup)
    task = store.task(owner, wf.root_task_id)
    with pytest.raises(StoreError, match='command'):
        command(store, owner, task, 'revise', state='succeeded')
    with pytest.raises(StoreError, match='scope'):
        store.events(AccessScope(actor_id='other', project_ids={'q'}), wf.id)
    assert store.task(owner, task.id) == task


def test_cancellation_advances_child_permission_semantics(setup):
    store, owner, _, _, _, _, _, permission = setup
    wf = workflow(setup)
    child = store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id), key='child')
    command(store, owner, store.task(owner, wf.root_task_id), 'cancel')
    cancelled = store.task(owner, child.id)
    assert cancelled.semantic_revision == child.semantic_revision + 1
    assert cancelled.state == 'cancelled' and not cancelled.permissions.approved


def test_draft_graph_still_has_default_structural_limits(setup):
    from flycode.domain.orchestration import TaskDraft, WorkflowDraft
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = store.create_workflow(owner, WorkflowDraft(project_id='p', task=TaskDraft(title='Draft')), key='unbudgeted')
    for i in range(8):
        store.create_task(owner, wf.id, TaskDraft(title='Child', parent_id=wf.root_task_id), key=f'draft-child{i}')
    with pytest.raises(StoreError, match='children'):
        store.create_task(owner, wf.id, TaskDraft(title='Ninth', parent_id=wf.root_task_id), key='draft-ninth')


def test_restart_during_stop_is_uncertain_and_stop_ack_can_be_replayed(setup):
    store, owner, runtime, _, instance, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    command(store, owner, store.task(owner, task.id), 'release_assignment')
    store.recover(runtime)
    assert store.attempts(owner, wf.id)[0].state == 'reconciling'
    assert store.budget(owner, wf.id).uncertain
    stopped = store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
    assert store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True) == stopped


def test_internal_aging_counts_only_confirmed_starts_within_one_workflow(setup):
    store, owner, runtime, profile, _, now, _, permission = setup
    wf = workflow(setup)
    root = store.task(owner, wf.root_task_id)
    command(store, owner, root, 'enqueue')
    old = store.create_task(owner, wf.id, draft(permission, parent_id=root.id, priority=0), key='old-child')
    command(store, owner, old, 'enqueue')
    group = store.create_task(owner, wf.id, draft(permission, parent_id=root.id), key='group')
    for i in range(8):
        now[0] += 1
        task = store.create_task(owner, wf.id, draft(permission, parent_id=group.id, priority=3), key=f'hi{i}')
        command(store, owner, task, 'enqueue')
    store.start_next(owner, key='internal-start')
    for i in range(7):
        instance = store.register_instance(runtime, profile.id)
        attempt = store.claim(runtime, request(instance, key=f'internal-{i}')).attempt
        assert attempt.task_id != old.id
        store.confirm_started(runtime, attempt.id, attempt.fence)
        store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed')
    instance = store.register_instance(runtime, profile.id)
    attempt = store.claim(runtime, request(instance, key='internal-eighth')).attempt
    assert attempt.task_id == old.id
    assert store.workflow(owner, wf.id).dispatch_count == 7
    assert store._counter('root_starts') == 1


def test_cosmetic_metadata_keeps_current_contract_and_active_semantic_change_is_rejected(setup):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, *_ = setup
    _, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    task = command(store, owner, store.task(owner, task.id), 'set_metadata', title='New name')
    assert task.semantic_revision == attempt.semantic_revision
    store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key='after-title')
    with pytest.raises(StoreError, match='ownership'):
        command(store, owner, task, 'revise', objective='A different task')


def test_active_root_cannot_be_hidden_while_child_is_owned(setup):
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, _, _, permission = setup
    wf = workflow(setup)
    root = command(store, owner, store.task(owner, wf.root_task_id), 'enqueue')
    child = store.create_task(owner, wf.id, draft(permission, parent_id=root.id), key='active-child')
    child = command(store, owner, child, 'enqueue')
    store.start_next(owner, key='child-workflow')
    assert store.claim(runtime, request(instance, child)).attempt is not None
    with pytest.raises(StoreError, match='ownership'):
        command(store, owner, root, 'archive')


def test_confirmed_stop_replay_does_not_release_twice(setup):
    store, owner, runtime, _, instance, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    result = store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed')
    assert store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed') == result
    assert sum(event.kind == 'attempt_stopped' for event in store.events(owner, wf.id)) == 1


def test_verification_can_use_protected_reserve_after_developer_consumes_its_share(setup):
    from flycode.domain.orchestration import (
        AgentProfileDraft,
        BudgetAmount,
        ExecutionPermission,
    )

    store, owner, runtime, profile, instance, _, _, permission = setup
    wf = workflow(setup)
    root = command(store, owner, store.task(owner, wf.root_task_id), 'enqueue')
    developer = store.create_task(owner, wf.id, draft(permission, parent_id=root.id,
        budget=BudgetAmount(model_calls=48, tool_calls=96)), key='development')
    developer = command(store, owner, developer, 'enqueue')
    read_only = ExecutionPermission(approved=True, tools=('read_file', 'run_tests'), read_scopes=('.',))
    reviewer = store.register_profile(owner, AgentProfileDraft(role='reviewer', model_ref=profile.model_ref,
        instructions='Verify the exact approved criteria.', capabilities=('python',), project_ids=('p',),
        permissions=read_only, context_limit=4096))
    review = store.create_task(owner, wf.id, draft(read_only, parent_id=root.id, kind='review',
        budget=BudgetAmount(model_calls=12, tool_calls=24)), key='verification')
    review = command(store, owner, review, 'enqueue')
    store.start_next(owner, key='reserve-workflow')
    dev_request = request(instance, developer).model_copy(update={'reserve': BudgetAmount(model_calls=48, tool_calls=96)})
    dev_attempt = store.claim(runtime, dev_request).attempt
    assert dev_attempt is not None
    store.confirm_started(runtime, dev_attempt.id, dev_attempt.fence)
    store.spend(runtime, dev_attempt.id, dev_attempt.fence, dev_attempt.reserved, key='development-spent')
    store.confirm_stopped(runtime, dev_attempt.id, dev_attempt.fence, outcome='failed')
    review_instance = store.register_instance(runtime, reviewer.id)
    review_request = request(review_instance, review, key='review-reserve').model_copy(
        update={'reserve': BudgetAmount(model_calls=12, tool_calls=24)})
    review_attempt = store.claim(runtime, review_request).attempt
    assert review_attempt is not None and review_attempt.verification
    assert store.budget(owner, wf.id).free == BudgetAmount()


def test_tracker_shows_assignment_then_real_start_and_clears_stopped_instance(setup):
    from flycode.tracker.service import TrackerService

    store, owner, runtime, _, instance, *_ = setup
    _, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    tracker = TrackerService(store)
    agent = tracker.agents(owner)[0]
    assert agent.instance.state == 'assigned'
    assert agent.task == tracker.task(owner, task.id).task
    assert agent.attempt == tracker.task(owner, task.id).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    assert tracker.agents(owner)[0].instance.state == 'running'
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed')
    agent = tracker.agents(owner)[0]
    assert agent.instance.state == 'idle' and agent.task is None and agent.attempt is None
    assert tracker.task(owner, task.id).attempt.id == attempt.id


def test_default_task_count_and_depth_limits_apply_to_real_draft_graph(setup):
    from flycode.orchestration.store import StoreError

    store, owner, _, _, _, _, _, permission = setup
    wf = workflow(setup)
    parents = [store.create_task(owner, wf.id, draft(permission, parent_id=wf.root_task_id),
                                key=f'parent-{i}') for i in range(8)]
    for i in range(55):
        store.create_task(owner, wf.id, draft(permission, parent_id=parents[i // 8].id), key=f'leaf-{i}')
    assert len(store.tasks(owner, wf.id)) == 64
    with pytest.raises(StoreError, match='task_limit'):
        store.create_task(owner, wf.id, draft(permission, parent_id=parents[-1].id), key='sixty-fifth')
    wf = workflow(setup)
    parent = wf.root_task_id
    for i in range(3):
        parent = store.create_task(owner, wf.id, draft(permission, parent_id=parent), key=f'depth-{i}').id
    with pytest.raises(StoreError, match='depth'):
        store.create_task(owner, wf.id, draft(permission, parent_id=parent), key='too-deep')


def test_heartbeat_extends_current_lease_without_resetting_deadline_or_budget(setup):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, now, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key='heartbeat-spent')
    now[0] = 1010.
    renewed = store.heartbeat(runtime, attempt.id, attempt.fence)
    assert renewed.lease_until == 1040.
    now[0] = 1031.
    assert store.expire(runtime) == []
    store.spend(runtime, attempt.id, attempt.fence, BudgetAmount(model_calls=1), key='after-renewal')
    assert store.workflow(owner, wf.id).deadline_at == 2800.
    assert store.budget(owner, wf.id).spent.model_calls == 2
    command(store, owner, store.task(owner, task.id), 'release_assignment')
    with pytest.raises(StoreError, match='fence'):
        store.heartbeat(runtime, attempt.id, attempt.fence)


def test_expired_worker_loses_fence_but_keeps_reservations_and_instance(setup):
    from flycode.orchestration.store import StoreError

    store, owner, runtime, profile, instance, now, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    now[0] = 1030.
    with pytest.raises(StoreError, match='fence'):
        store.heartbeat(runtime, attempt.id, attempt.fence)
    expired = store.expire(runtime)
    assert len(expired) == 1 and expired[0].state == 'reconciling'
    assert store.budget(owner, wf.id).reserved.model_calls == 2
    assert store.budget(owner, wf.id).uncertain
    assert store.task(owner, task.id).current_attempt_id == attempt.id
    assert store.instance(runtime, instance.id).state == 'reconciling'
    assert store.expire(runtime) == []
    replacement = store.register_instance(runtime, profile.id)
    result = store.claim(runtime, request(replacement, store.task(owner, task.id), key='after-expiry'))
    assert result.attempt is None and 'task_not_ready' in result.reasons
    with pytest.raises(StoreError, match='uncertain'):
        store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait')


def test_workflow_deadline_revokes_even_healthy_worker_and_retains_slot_until_stop(setup):
    from flycode.orchestration.store import StoreError

    store, owner, runtime, _, instance, now, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    for timestamp in range(1010, 2800, 10):
        now[0] = float(timestamp)
        store.heartbeat(runtime, attempt.id, attempt.fence)
    now[0] = 2800.
    expired = store.expire(runtime)
    assert len(expired) == 1
    assert store.workflow(owner, wf.id).state == 'incomplete'
    assert store.workflow(owner, wf.id).active_slot
    with pytest.raises(StoreError, match='fence|wall_deadline'):
        store.heartbeat(runtime, attempt.id, attempt.fence)
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
    assert not store.workflow(owner, wf.id).active_slot
    assert store.workflow(owner, wf.id).deadline_at == 2800.


def test_user_cancellation_wins_over_simultaneous_workflow_deadline(setup):
    store, owner, runtime, _, instance, now, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    command(store, owner, store.task(owner, task.id), 'cancel')
    now[0] = 2800.
    store.expire(runtime)
    assert store.workflow(owner, wf.id).state == 'cancelling'
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
    assert store.workflow(owner, wf.id).state == 'cancelled'
    assert not store.workflow(owner, wf.id).active_slot


def test_lease_expiration_is_scoped_durable_and_not_resurrected_by_other_connection(setup):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import Store, StoreError

    store, owner, runtime, _, instance, now, catalogue, *_ = setup
    wf, task = queued(setup)
    attempt = store.claim(runtime, request(instance, task)).attempt
    store.confirm_started(runtime, attempt.id, attempt.fence)
    now[0] = 1030.
    foreign = AccessScope(actor_id='host', project_ids={'q'}, role='runtime')
    assert store.expire(foreign) == []
    with pytest.raises(StoreError, match='scope'):
        store.heartbeat(owner, attempt.id, attempt.fence)
    assert len(store.expire(runtime)) == 1
    second = Store(store.path, catalogue=catalogue, clock=lambda: now[0])
    try:
        with pytest.raises(StoreError, match='fence'):
            second.heartbeat(runtime, attempt.id, attempt.fence)
        assert second.budget(owner, wf.id).uncertain
        assert second.instance(runtime, instance.id).state == 'reconciling'
        assert second.expire(runtime) == []
        events = [event for event in second.events(owner, wf.id) if event.kind == 'attempt_reconciling']
        assert len(events) == 1 and events[0].data['reason'] == 'lease_expired'
    finally:
        second.close()
