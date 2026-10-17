"""Owner-bound automatic worker dispatch uses canonical tasks and actual effects."""

import json
import time

import pytest
import test_store
from test_store import command, queued, workflow

setup = test_store.setup


def source_tree(tmp_path):
    source = tmp_path / 'owner'
    (source / 'src').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'src/parser.py').write_text('value = 0\n')
    (source / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n'
        '    assert runpy.run_path("src/parser.py")["value"] == 1\n')
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff original bytes\r\n')
    return source


def test_owner_binding_is_durable_inert_and_captures_exact_source(setup, tmp_path):
    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.store import Store
    from flycode.verification.snapshot import capture

    store, owner, host, _, _, _, catalogue, _ = setup
    wf = workflow(setup)
    task = store.task(owner, wf.root_task_id)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    bound = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    assert bound.contract_hash == task.contract_hash and bound.source_hash == capture(source).subject_hash
    assert bound.source_root == str(source.resolve()) and bound.owner_id == owner.actor_id
    assert dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind') == bound
    assert store.task(owner, task.id).state == 'planned' and not store.attempts(owner, wf.id)
    assert store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0] == 0
    assert not (source / '.git').exists()
    reopened = Store(store.path, catalogue=catalogue, clock=store.clock)
    try:
        assert Dispatcher(reopened, dispatcher.root).binding(host, task.id) == bound
    finally:
        reopened.close()


@pytest.mark.parametrize('authority', ['reader', 'runtime', 'foreign'])
def test_binding_rejects_nonowner_authority_before_source_read(setup, tmp_path, monkeypatch, authority):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration import dispatch
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = workflow(setup)
    task = store.task(owner, wf.root_task_id)
    scope = AccessScope(actor_id='caller', project_ids={'q'} if authority == 'foreign' else {'p'},
        role='owner' if authority == 'foreign' else authority)
    monkeypatch.setattr(dispatch, 'capture', lambda *a, **k: pytest.fail('Unauthorized source inspection'))
    with pytest.raises(StoreError, match='scope'):
        dispatch.Dispatcher(store, tmp_path / 'runtime').bind(scope, task.id, tmp_path / 'secret',
            expected_revision=task.revision, key='foreign')
    assert not store.attempts(owner, wf.id)


def test_changed_task_cannot_dispatch_under_previous_source_approval(setup, tmp_path):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, task = queued(setup)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    dispatcher.bind(owner, task.id, source_tree(tmp_path), expected_revision=task.revision, key='bind')
    command(store, owner, task, 'revise', objective='Repair a different parser behavior.')
    result = dispatcher.tick(host, key='stale-approval')
    assert result.attempt is None and 'execution_binding_stale' in result.reasons
    assert not store.attempts(owner, wf.id) and store.budget(owner, wf.id).reserved == BudgetAmount()


def test_changed_source_cannot_consume_assignment_or_budget(setup, tmp_path):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    (source / 'src/parser.py').write_text('value = 2\n')
    result = dispatcher.tick(host, key='changed-source')
    assert result.attempt is None and 'snapshot_changed' in result.reasons
    assert not store.attempts(owner, wf.id)
    assert store.budget(owner, wf.id).spent == store.budget(owner, wf.id).reserved == BudgetAmount()
    assert store.workflow(owner, wf.id).dispatch_count == 0


def test_ready_unbound_task_does_not_launch_worker(setup, tmp_path):
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, _ = queued(setup)
    result = Dispatcher(store, tmp_path / 'runtime').tick(host, key='unbound')
    assert result.attempt is None and 'worker_binding_required' in result.reasons
    assert not store.attempts(owner, wf.id)
    assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0


def test_dispatcher_launches_real_worker_without_manual_claim(setup, tmp_path, model_server):
    from flycode.domain.orchestration import AgentProfileDraft, BudgetAmount
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, original, *_ = setup
    store.clock = time.time
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **original.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 8192}))
    assert profile.available and profile.context_limit == profile.context_capacity == 8192
    store.register_instance(host, profile.id)
    wf, task = queued(setup, budget=BudgetAmount(model_calls=2, tool_calls=3), allowed_profile_ids=(profile.id,))
    source = source_tree(tmp_path)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    bound = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind', brain='heuristic_fsm')
    assigned = dispatcher.tick(host, key='dispatch')
    assert assigned.attempt is not None and assigned.attempt.execution_binding_id == bound.id
    handle = dispatcher.handles[assigned.attempt.id]
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
        artifact = Artifacts(store, dispatcher.root).for_attempt(owner, assigned.attempt.id)
        assert (dispatcher.root / 'artifacts' / artifact.id / 'target/src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff original bytes\r\n'
        budget = store.budget(owner, wf.id)
        assert 1 <= budget.spent.model_calls <= 2 and 1 <= budget.spent.tool_calls <= 3
        assert budget.reserved == BudgetAmount() and not budget.uncertain
        assert len(store.attempts(owner, wf.id)) == 1
        replay = dispatcher.tick(host, key='dispatch')
        assert replay == assigned and len(store.attempts(owner, wf.id)) == 1
        assert store.budget(owner, wf.id) == budget
    finally:
        handle.close()


def test_removed_source_blocks_tick_without_new_attempt(setup, tmp_path):
    import shutil

    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    approved = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    shutil.rmtree(source)
    result = dispatcher.tick(host, key='source-gone')
    assert result.attempt is None and 'source_unavailable' in result.reasons
    assert dispatcher.binding(owner, task.id) == approved
    assert not store.attempts(owner, wf.id) and store.budget(owner, wf.id).reserved == BudgetAmount()


def test_missing_folder_binding_reports_failure_without_read_or_record(setup, tmp_path):
    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.store import StoreError

    store, owner, *_ = setup
    wf = workflow(setup)
    task = store.task(owner, wf.root_task_id)
    with pytest.raises(StoreError, match='source_unavailable'):
        Dispatcher(store, tmp_path / 'runtime').bind(owner, task.id, tmp_path / 'missing',
            expected_revision=task.revision, key='missing-source')
    assert store.db.execute('SELECT COUNT(*) FROM worker_bindings').fetchone()[0] == 0
    assert not store.attempts(owner, wf.id)


def test_global_priority_wins_even_when_compatible_instance_sorts_last(setup, tmp_path):
    from test_store import draft

    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, original, instance, *_ = setup
    other = store.register_profile(owner, AgentProfileDraft.model_validate({
        **original.model_dump(include=set(AgentProfileDraft.model_fields)), 'capabilities': ('other',)}))
    second = store.register_instance(host, other.id)
    profiles = {original.id: original, other.id: other}
    first, last = sorted((instance, second), key=lambda item: item.id)
    wf, parent = queued(setup)
    low = store.create_task(owner, wf.id, draft(original.permissions, parent_id=parent.id,
        capabilities=profiles[first.profile_id].capabilities, priority=0), key='low')
    high = store.create_task(owner, wf.id, draft(original.permissions, parent_id=parent.id,
        capabilities=profiles[last.profile_id].capabilities, priority=3), key='high')
    low, high = [command(store, owner, item, 'enqueue') for item in (low, high)]
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    source = source_tree(tmp_path)
    bindings = [dispatcher.bind(owner, item.id, source, expected_revision=item.revision, key='bind-'+item.id)
        for item in (low, high)]
    selected = store.dispatch_bound(host, runtime_root=str(dispatcher.root), binding_ids=tuple(b.id for b in bindings), key='global')
    assert selected.attempt is not None and selected.attempt.task_id == high.id and selected.attempt.instance_id == last.id
    assert selected.attempt.initial_budget == high.budget
    assert store.dispatch_bound(host, runtime_root=str(dispatcher.root), binding_ids=(), key='global') == selected
    assert len(store.attempts(owner, wf.id)) == 1


def test_two_dispatch_connections_create_one_owned_attempt(setup, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.store import Store

    store, owner, host, _, _, _, catalogue, _ = setup
    wf, task = queued(setup)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    binding = dispatcher.bind(owner, task.id, source_tree(tmp_path), expected_revision=task.revision, key='bind')
    ready = Barrier(2)
    def claim(number):
        connection = Store(store.path, catalogue=catalogue, clock=store.clock)
        try:
            ready.wait(timeout=5)
            return connection.dispatch_bound(host, runtime_root=str(dispatcher.root), binding_ids=(binding.id,), key='race-'+str(number))
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, range(2)))
    assert sum(result.attempt is not None for result in outcomes) == 1
    assert len(store.attempts(owner, wf.id)) == 1
    assert store.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 1
    assert len([event for event in store.events(owner, wf.id) if event.kind == 'worker_dispatched']) == 1
    assert store.workflow(owner, wf.id).dispatch_count == 0


def test_rebinding_requires_previous_approval_and_preserves_history(setup, tmp_path):
    from flycode.domain.orchestration import WorkerBinding
    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.store import StoreError

    store, owner, host, *_ = setup
    _, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    first = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind-first')
    (source / 'src/parser.py').write_text('value = 2\n')
    with pytest.raises(StoreError, match='binding_revision'):
        dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='blind-rebind')
    second = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, expected_binding_id=first.id, key='rebind')
    assert second.revision == 2 and second.previous_id == first.id and second.source_hash != first.source_hash
    assert dispatcher.binding(owner, task.id) == second
    assert store._load('worker_bindings', first.id, WorkerBinding) == first
    assert dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind-first') == first
    assert dispatcher.binding(owner, task.id) == second
    stale = store.dispatch_bound(host, runtime_root=str(dispatcher.root), binding_ids=(first.id,), key='stale-binding-id')
    assert stale.attempt is None and 'binding_unverified' in stale.reasons


def test_source_change_after_claim_prevents_spawn_and_preserves_owner_bytes(setup, tmp_path, monkeypatch):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    launch = dispatcher.runtime.launch
    def change_then_launch(*args, **kwargs):
        (source / 'src/parser.py').write_text('value = 2\n')
        return launch(*args, **kwargs)
    monkeypatch.setattr(dispatcher.runtime, 'launch', change_then_launch)
    result = dispatcher.tick(host, key='source-race')
    assert result.attempt is not None
    current = store._attempt(result.attempt.id)
    assert current.state == 'failed' and current.stop_confirmed
    assert not dispatcher.handles
    assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM execution_lanes').fetchone()[0] == 0
    assert store.budget(owner, wf.id).spent == store.budget(owner, wf.id).reserved == BudgetAmount()
    assert (source / 'src/parser.py').read_text() == 'value = 2\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff original bytes\r\n'
    blocked = next(e for e in store.events(owner, wf.id) if e.kind == 'worker_dispatch_launch_blocked')
    assert blocked.data['reason'] == 'snapshot_changed'


def test_schema11_upgrade_keeps_old_attempt_bytes_reservations_and_commands(setup):
    from test_store import request

    from flycode.orchestration.store import Store

    store, owner, host, _, instance, _, catalogue, _ = setup
    wf, task = queued(setup)
    original = store.claim(host, request(instance, task)).attempt
    assert original is not None
    raw = json.loads(store.db.execute('SELECT data FROM attempts WHERE id=?', (original.id,)).fetchone()[0])
    raw.pop('execution_binding_id')
    store.db.execute('UPDATE attempts SET data=? WHERE id=?', (json.dumps(raw), original.id))
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
        for table in ('attempts', 'reservations', 'commands', 'events', 'profiles', 'instances')}
    store.db.execute('DROP TABLE worker_bindings')
    store.db.execute('PRAGMA user_version=11')
    connection = Store(store.path, catalogue=catalogue, clock=store.clock)
    try:
        assert connection.db.execute('PRAGMA user_version').fetchone()[0] == 15
        assert connection._attempt(original.id).execution_binding_id is None
        assert connection.budget(owner, wf.id) == store.budget(owner, wf.id)
        assert connection.db.execute('SELECT COUNT(*) FROM worker_bindings').fetchone()[0] == 0
        after = {table: [tuple(row) for row in connection.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
            for table in before}
        assert after == before
    finally:
        connection.close()


def test_schema12_migration_failure_rolls_back_new_table_and_version(setup, monkeypatch):
    import sqlite3

    from flycode.orchestration.store import Store

    store, owner, *_ = setup
    wf, _ = queued(setup)
    budget = store.budget(owner, wf.id)
    store.db.execute('DROP TABLE worker_bindings')
    store.db.execute('PRAGMA user_version=11')
    connect = sqlite3.connect
    def interrupted_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '12' else sqlite3.SQLITE_OK)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', interrupted_connect)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        Store(store.path, catalogue=store.catalogue, clock=store.clock)
    probe = connect(store.path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 11
        assert probe.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='worker_bindings'").fetchone()[0] == 0
    finally:
        probe.close()
    assert store.budget(owner, wf.id) == budget


@pytest.mark.parametrize(('operation', 'attempt_state', 'workflow_state'), [
    ('pause', 'interrupted', 'paused'), ('cancel', 'cancelled', 'cancelled')])
def test_owner_stop_between_claim_and_spawn_confirms_no_effect_and_keeps_spend(setup, tmp_path, monkeypatch,
        operation, attempt_state, workflow_state):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.dispatch import Dispatcher

    store, owner, host, *_ = setup
    wf, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    before = store.budget(owner, wf.id)
    deadline = store.workflow(owner, wf.id).deadline_at
    launch = dispatcher.runtime.launch
    def stop_then_launch(*args, **kwargs):
        command(store, owner, store.task(owner, task.id), operation)
        return launch(*args, **kwargs)
    monkeypatch.setattr(dispatcher.runtime, 'launch', stop_then_launch)
    result = dispatcher.tick(host, key='stop-before-spawn')
    assert result.attempt is not None
    attempt = store._attempt(result.attempt.id)
    assert attempt.state == attempt_state and attempt.stop_confirmed
    assert store.workflow(owner, wf.id).state == workflow_state
    assert store.workflow(owner, wf.id).deadline_at == deadline
    assert store.budget(owner, wf.id).spent == before.spent == BudgetAmount()
    assert store.budget(owner, wf.id).reserved == BudgetAmount() and not store.budget(owner, wf.id).uncertain
    assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM execution_lanes').fetchone()[0] == 0
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def test_binding_replay_survives_removed_folder_without_new_source_inspection(setup, tmp_path, monkeypatch):
    import shutil

    from flycode.orchestration import dispatch

    store, owner, host, *_ = setup
    workflow, task = queued(setup)
    source = source_tree(tmp_path)
    dispatcher = dispatch.Dispatcher(store, tmp_path / 'runtime')
    first = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    shutil.rmtree(source)
    with monkeypatch.context() as patch:
        patch.setattr(dispatch, 'capture', lambda *a, **k: pytest.fail('Replay recaptured an unavailable source'))
        assert dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind') == first
    assert store.db.execute('SELECT COUNT(*) FROM worker_bindings').fetchone()[0] == 1
    assert not store.attempts(owner, workflow.id)
    result = dispatcher.tick(host, key='unavailable-replay-source')
    assert result.attempt is None and 'source_unavailable' in result.reasons
