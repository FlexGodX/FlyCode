"""Exact owner-approved review bindings run real checks through canonical dispatch."""
import shutil

import pytest
from test_result_delivery import checks, completed, delivery, repair  # noqa: F401

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
)
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.dispatch import Dispatcher
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def review_ready(owned, model_server, tmp_path):
    store, owner, host, workflow, author, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'exact-review-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(author.acceptance, checks(), oracle)
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    profile = store.register_profile(owner, AgentProfileDraft(role='reviewer',
        model_ref=attempt.profile_snapshot.model_ref, instructions='Verify the exact approved artifact.',
        project_ids=(author.project_id,), permissions=permission, context_limit=8192))
    task = store.create_task(owner, workflow.id, TaskDraft(title='Review exact repair', kind='review',
        objective='Verify every unchanged owner criterion against the pinned artifact.', parent_id=author.id,
        acceptance=author.acceptance, permissions=permission, budget=BudgetAmount(tool_calls=4),
        input_artifacts={artifact.id: artifact.patch_hash}), key='review-draft')
    store.register_instance(host, profile.id)
    return Dispatcher(store, runtime.root), artifact, approval, task, engine, oracle


def bind(review_ready, owned):
    dispatcher, artifact, approval, task, *_ = review_ready
    _, owner, *_ = owned
    return dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='bind-review')


def test_review_binding_is_inert_exact_and_durable(review_ready, owned):
    from flycode.orchestration.store import Store

    dispatcher, artifact, approval, task, *_ = review_ready
    store, owner, host, workflow, _, author, _ = owned
    before = store.budget(owner, workflow.id)
    binding = bind(review_ready, owned)
    assert binding.artifact_id == artifact.id and binding.patch_hash == artifact.patch_hash
    assert binding.author_attempt_id == author.id and binding.author_instance_id == author.instance_id
    assert binding.approval_hash == approval and binding.contract_hash == task.contract_hash
    assert bind(review_ready, owned) == binding
    assert len(store.attempts(owner, workflow.id)) == 1
    assert store.task(owner, task.id).state == 'planned' and store.budget(owner, workflow.id) == before
    connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
    try:
        assert Dispatcher(connection, dispatcher.root).review_binding(host, task.id) == binding
    finally:
        connection.close()


@pytest.mark.parametrize('role', ['reader', 'runtime', 'foreign'])
def test_review_binding_denies_authority_before_artifact_read(review_ready, owned, monkeypatch, role):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    dispatcher, artifact, approval, task, *_ = review_ready
    scope = AccessScope(actor_id='caller', project_ids={'elsewhere'} if role == 'foreign' else {task.project_id},
        role='owner' if role == 'foreign' else role)
    monkeypatch.setattr(Artifacts, 'get', lambda *a, **kw: pytest.fail('Unauthorized artifact read'))
    with pytest.raises(StoreError, match='scope'):
        dispatcher.bind_review(scope, task.id, artifact.id, approval_hash=approval,
            expected_revision=task.revision, key='foreign-review')


def test_review_tick_executes_real_checks_without_manual_review_claim(review_ready, owned):
    from flycode.orchestration.review import Reviews

    dispatcher, artifact, _, task, *_ = review_ready
    store, owner, host, workflow, _, author, source = owned
    binding = bind(review_ready, owned)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='dispatch-review')
    assert result.attempt is not None and result.attempt.execution_binding_id == binding.id
    assert result.attempt.instance_id != author.instance_id
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (result.attempt.id,)).fetchone()
    review = Reviews(store, dispatcher.root).get(owner, row['id'])
    assert review.status == 'approved' and review.verdict.status == 'passed' and review.artifact_id == artifact.id
    assert store.task(owner, task.id).state == 'succeeded'
    assert store._attempt(author.id).state == 'awaiting_review'
    after = store.budget(owner, workflow.id)
    assert after.spent.model_calls == before.spent.model_calls
    assert after.spent.tool_calls == before.spent.tool_calls + 4
    assert after.reserved == BudgetAmount() and not after.uncertain
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert dispatcher.tick(host, key='dispatch-review') == result
    assert store.budget(owner, workflow.id) == after
    assert len(store.attempts(owner, workflow.id)) == 2


def test_superseded_oracle_blocks_review_before_assignment(review_ready, owned):
    dispatcher, _, approval, task, engine, oracle = review_ready
    store, owner, host, workflow, *_ = owned
    bind(review_ready, owned)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    updated = checks();updated[0] = updated[0].model_copy(update={'timeout': 29})
    assert engine.oracles.approve(task.acceptance, updated, oracle) != approval
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='old-oracle')
    assert result.attempt is None and 'oracle_changed' in result.reasons
    assert store.budget(owner, workflow.id) == before and len(store.attempts(owner, workflow.id)) == 1


def test_review_binding_replay_retains_history_but_tampered_artifact_cannot_dispatch(review_ready, owned):
    dispatcher, artifact, _, task, *_ = review_ready
    store, owner, host, workflow, *_ = owned
    original = bind(review_ready, owned)
    path = dispatcher.root / 'artifacts' / artifact.id / 'target/src/example.py'
    path.write_text('answer = 9\n')
    assert bind(review_ready, owned) == original
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='tampered-review-artifact')
    assert result.attempt is None and 'artifact_integrity' in result.reasons
    assert store.budget(owner, workflow.id) == before and len(store.attempts(owner, workflow.id)) == 1


def test_changed_review_contract_needs_new_binding_before_assignment(review_ready, owned):
    dispatcher, _, _, task, *_ = review_ready
    store, owner, host, workflow, *_ = owned
    bind(review_ready, owned)
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='revise-review', changes={'objective': 'Verify a newly requested behavior.'}))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='stale-review-binding')
    assert result.attempt is None and 'execution_binding_stale' in result.reasons
    assert store.budget(owner, workflow.id) == before and len(store.attempts(owner, workflow.id)) == 1


def test_schema12_upgrade_preserves_worker_bindings_and_canonical_history(review_ready, owned):
    from flycode.orchestration.store import Store

    dispatcher, _, _, _, *_ = review_ready
    store, owner, host, workflow, author, _, source = owned
    task = store.create_task(owner, workflow.id, TaskDraft(title='Approved next worker',
        objective=author.objective, parent_id=author.id, acceptance=author.acceptance,
        permissions=author.permissions, budget=BudgetAmount(model_calls=1, tool_calls=1)), key='old-bound-worker')
    binding = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind-old-worker')
    tables = ('worker_bindings', 'attempts', 'reservations', 'events', 'commands', 'profiles', 'instances')
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in tables}
    store.db.execute('DROP TABLE review_bindings')
    store.db.execute('PRAGMA user_version=12')
    connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
    try:
        assert connection.db.execute('PRAGMA user_version').fetchone()[0] == 17
        assert connection.db.execute('SELECT COUNT(*) FROM review_bindings').fetchone()[0] == 0
        assert Dispatcher(connection, dispatcher.root).binding(host, task.id) == binding
        after = {table: [tuple(row) for row in connection.db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in tables}
        assert after == before
    finally:
        connection.close()


def test_schema13_migration_failure_preserves_schema12_and_budget(review_ready, owned, monkeypatch):
    import sqlite3

    from flycode.orchestration.store import Store

    _, _, _, _, *_ = review_ready
    store, owner, _, workflow, *_ = owned
    before = store.budget(owner, workflow.id)
    store.db.execute('DROP TABLE review_bindings')
    store.db.execute('PRAGMA user_version=12')
    connect = sqlite3.connect
    def interrupted(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '13' else sqlite3.SQLITE_OK)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', interrupted)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        Store(store.path, catalogue=store.catalogue, clock=store.clock)
    probe = connect(store.path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 12
        assert probe.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('review_bindings','current_review_binding')").fetchone()[0] == 0
    finally:
        probe.close()
    assert store.budget(owner, workflow.id) == before


def test_oracle_revoked_after_dispatch_admission_cannot_leave_unstarted_checks_owned(review_ready, owned, monkeypatch):
    from flycode.orchestration.review import Reviews

    dispatcher, _, _, task, engine, oracle = review_ready
    store, owner, host, workflow, _, _, source = owned
    bind(review_ready, owned)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    before = store.budget(owner, workflow.id)
    run = Reviews.run
    def revoke_then_run(self, *args, **kwargs):
        updated = checks();updated[0] = updated[0].model_copy(update={'timeout': 29})
        engine.oracles.approve(task.acceptance, updated, oracle)
        return run(self, *args, **kwargs)
    monkeypatch.setattr(Reviews, 'run', revoke_then_run)
    result = dispatcher.tick(host, key='oracle-admission-race')
    assert result.attempt is not None
    current = store._attempt(result.attempt.id)
    assert current.state == 'failed' and current.stop_confirmed
    assert store.budget(owner, workflow.id).spent == before.spent
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert store.db.execute('SELECT COUNT(*) FROM tool_calls WHERE attempt_id=?', (current.id,)).fetchone()[0] == 0
    assert not store.db.execute("SELECT 1 FROM reviews WHERE attempt_id=? AND json_extract(data,'$.status')='approved'", (current.id,)).fetchone()
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_denied_review_binding_does_not_create_host_validation_storage(owned, tmp_path):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    store, _, _, _, task, _, _ = owned
    root = tmp_path / 'not-created-review-host'
    dispatcher = Dispatcher(store, root)
    reader = AccessScope(actor_id='reader', role='reader', project_ids={task.project_id})
    with pytest.raises(StoreError, match='scope'):
        dispatcher.bind_review(reader, task.id, 'untrusted-artifact', approval_hash='0' * 64,
            expected_revision=task.revision, key='denied-fresh-host')
    assert not root.exists()


def test_review_stop_is_responsive_and_retains_started_check_uncertainty(review_ready, owned, tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import Store
    from flycode.orchestration.tools import Tools
    from flycode.verification.custody import stopped

    dispatcher, artifact, _, task, engine, _ = review_ready
    store, owner, host, workflow, _, _, source = owned
    marker = tmp_path / 'review-target-started'
    oracle = tmp_path / 'stop-oracle'
    (oracle / 'tests').mkdir(parents=True)
    (oracle / 'tests/test_example.py').write_text('import pathlib, runpy, time\n'
        'def test_example():\n'
        '    value = runpy.run_path("src/example.py")["answer"]\n'
        '    if value == 1:\n'
        f'        pathlib.Path({str(marker)!r}).write_text("started")\n'
        '        time.sleep(60)\n'
        '    assert value == 1\n')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    binding = dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='bind-stop-review')
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-review'))
    before = store.budget(owner, workflow.id)
    deadline = store.workflow(owner, workflow.id).deadline_at
    def run_review():
        connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
        try:
            return Dispatcher(connection, dispatcher.root).tick(host, key='dispatch-slow-review')
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_review)
        until = time.monotonic() + 15
        while not marker.exists() and not future.done() and time.monotonic() < until:
            time.sleep(.01)
        assert marker.exists(), future.result(timeout=1) if future.done() else 'Target process did not start'
        current = store.task(owner, task.id)
        start = time.monotonic()
        store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
            idempotency_key='pause-running-review'))
        assert time.monotonic() - start < 1
        result = future.result(timeout=10)
    assert result.attempt is not None and result.attempt.execution_binding_id == binding.id
    attempt = store._attempt(result.attempt.id)
    assert attempt.state in {'stopping', 'reconciling'} and not attempt.stop_confirmed
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == attempt.id]
    assert len(calls) == 2 and any(call.status == 'uncertain' for call in calls)
    for call in calls:
        assert call.process is not None
        with stopped(call.process):
            pass
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (attempt.id,)).fetchone()
    review = Reviews(store, dispatcher.root).get(owner, row['id'])
    assert review.status == 'inconclusive' and review.verdict.status != 'passed'
    after = store.budget(owner, workflow.id)
    assert after.spent.model_calls == before.spent.model_calls
    assert after.spent.tool_calls == before.spent.tool_calls + 2
    assert after.uncertain and store.workflow(owner, workflow.id).deadline_at == deadline
    assert store.instance(host, attempt.instance_id).current_attempt_id == attempt.id
    assert dispatcher.tick(host, key='after-review-stop').attempt is None
    assert len(store.attempts(owner, workflow.id)) == 2
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_fresh_review_can_dispatch_retained_producer_after_root_reopen(owned, delivery, completed):  # noqa: F811
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, author, producer, source = owned
    runtime, artifact, _, approval = delivery
    service, previous, old_integrator = completed
    current = store.task(owner, author.id)
    current = store.command(owner, current.id, TaskCommand(operation='reopen', expected_revision=current.revision,
        idempotency_key='reopen-for-dispatched-review'))
    store.command(owner, current.id, TaskCommand(operation='enqueue', expected_revision=current.revision,
        idempotency_key='enqueue-reopened-root'))
    store.start_next(owner, key='restart-for-dispatched-review')
    permission = old_integrator.profile_snapshot.permissions
    task = store.create_task(owner, workflow.id, TaskDraft(title='Fresh retained artifact review', kind='review',
        objective='Recheck the retained artifact against the current owner criteria.', parent_id=author.id,
        acceptance=current.acceptance, permissions=permission, budget=BudgetAmount(tool_calls=4),
        input_artifacts={artifact.id: artifact.patch_hash}), key='fresh-retained-review')
    dispatcher = Dispatcher(store, runtime.root)
    before = store.budget(owner, workflow.id)
    binding = dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='bind-fresh-retained-review')
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-fresh-retained-review'))
    result = dispatcher.tick(host, key='dispatch-fresh-retained-review')
    assert result.attempt is not None and result.attempt.execution_binding_id == binding.id
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (result.attempt.id,)).fetchone()
    review = Reviews(store, runtime.root).get(owner, row['id'])
    assert review.status == 'approved' and review.id not in previous.review_ids
    assert artifact.semantic_revision != current.semantic_revision
    assert store._attempt(producer.id).state == 'succeeded'
    assert service.get(owner, previous.id).state == 'invalidated' and not service.current(owner, previous.id)
    assert store.budget(owner, workflow.id).spent.tool_calls == before.spent.tool_calls + 4
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
