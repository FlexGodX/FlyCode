"""Canonical integration dispatch reuses the exact reviewed delivery service."""
import pytest
from test_result_delivery import completed, delivery  # noqa: F401

from flycode.domain.orchestration import BudgetAmount, TaskCommand, TaskDraft
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.integration import Integration


@pytest.fixture
def integration_ready(owned, delivery):  # noqa: F811
    store, owner, _, workflow, author, _, _ = owned
    runtime, artifact, review, approval = delivery
    old_reviewer = store._attempt(review.reviewer_attempt_id)
    task = store.create_task(owner, workflow.id, TaskDraft(title='Integrate reviewed repair', kind='review',
        objective='Apply only the reviewed repair and verify the complete current result.', parent_id=author.id,
        acceptance=author.acceptance, permissions=old_reviewer.profile_snapshot.permissions,
        budget=BudgetAmount(tool_calls=24), input_artifacts={artifact.id: artifact.patch_hash}), key='integration-draft')
    return Dispatcher(store, runtime.root), artifact, review, approval, task


def bind(integration_ready, owned):
    dispatcher, artifact, review, approval, task = integration_ready
    _, owner, _, _, author, *_ = owned
    return dispatcher.bind_integration(owner, task.id, (artifact.id,), (review.id,),
        approval_hashes={author.id: approval, task.id: approval}, expected_revision=task.revision, key='bind-integration')


def test_integration_binding_is_inert_and_restart_preserves_exact_authority(integration_ready, owned):
    from flycode.orchestration.store import Store

    dispatcher, artifact, review, _, task = integration_ready
    store, owner, host, workflow, *_ = owned
    before = store.budget(owner, workflow.id)
    binding = bind(integration_ready, owned)
    assert binding.mode == 'integration' and binding.artifact_hashes == {artifact.id: artifact.patch_hash}
    assert binding.review_ids == (review.id,) and binding.semantic_revision == task.semantic_revision
    assert bind(integration_ready, owned) == binding
    assert len(store.attempts(owner, workflow.id)) == 2 and store.budget(owner, workflow.id) == before
    connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
    try:
        assert Dispatcher(connection, dispatcher.root).integration_binding(host, task.id) == binding
    finally:
        connection.close()


def test_integration_tick_applies_after_real_checks_and_has_current_completion(integration_ready, owned, monkeypatch):
    dispatcher, artifact, review, _, task = integration_ready
    store, owner, host, workflow, author, _, source = owned
    binding = bind(integration_ready, owned)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-integrator'))
    before = store.budget(owner, workflow.id)
    deadline = store.workflow(owner, workflow.id).deadline_at
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Integration used manual claim'))
    result = dispatcher.tick(host, key='dispatch-integrator')
    assert result.attempt is not None and result.attempt.execution_binding_id == binding.id
    row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (result.attempt.id,)).fetchone()
    service = Integration(store, dispatcher.root)
    integrated = service.get(owner, row['id'])
    assert integrated.state == 'completed' and service.current(owner, integrated.id)
    assert integrated.artifact_hashes == {artifact.id: artifact.patch_hash} and integrated.review_ids == (review.id,)
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    assert all(item.state == 'succeeded' and item.result_id == integrated.id for item in store.tasks(owner, workflow.id))
    after = store.budget(owner, workflow.id)
    assert after.spent.model_calls == before.spent.model_calls
    assert after.spent.tool_calls == before.spent.tool_calls + 17
    assert after.reserved == BudgetAmount() and not after.uncertain
    assert store.workflow(owner, workflow.id).deadline_at == deadline
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert dispatcher.tick(host, key='dispatch-integrator') == result
    assert store.budget(owner, workflow.id) == after
    assert store.task(owner, author.id).current_attempt_id is None


@pytest.mark.parametrize('change', ['source', 'review-evidence', 'criteria'])
def test_changed_integration_inputs_block_before_assignment(integration_ready, owned, change):
    dispatcher, _, review, _, task = integration_ready
    store, owner, host, workflow, _, _, source = owned
    bind(integration_ready, owned)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-integrator'))
    if change == 'source':
        (source / 'user-new-file.txt').write_text('keep owner bytes')
    elif change == 'review-evidence':
        service = Integration(store, dispatcher.root)
        (service.engine.data_dir / review.verdict.evidence_id / 'manifest.json').write_text('changed evidence')
    else:
        current = store.task(owner, task.id)
        store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='revise-author', changes={'acceptance': {**current.acceptance.model_dump(), 'version': 2}}))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='changed-integration-inputs')
    assert result.attempt is None and result.reasons
    assert len(store.attempts(owner, workflow.id)) == 2 and store.budget(owner, workflow.id) == before
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('role', ['reader', 'runtime', 'foreign'])
def test_integration_binding_rejects_untrusted_authority_before_storage(owned, tmp_path, monkeypatch, role):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.store import StoreError

    store, _, _, _, task, *_ = owned
    scope = AccessScope(actor_id='caller', project_ids={'elsewhere'} if role == 'foreign' else {task.project_id},
        role='owner' if role == 'foreign' else role)
    root = tmp_path / 'denied-integration-host'
    monkeypatch.setattr(Artifacts, 'get', lambda *a, **kw: pytest.fail('Unauthorized artifact read'))
    with pytest.raises(StoreError, match='scope'):
        Dispatcher(store, root).bind_integration(scope, task.id, ('unknown',), ('unknown-review',),
            approval_hashes={}, expected_revision=task.revision, key='denied-integration')
    assert not root.exists()


def test_artifact_record_changed_after_deep_verification_cannot_be_claimed(integration_ready, owned, monkeypatch):
    import json

    dispatcher, artifact, _, _, task = integration_ready
    store, owner, host, workflow, *_ = owned
    bind(integration_ready, owned)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-integrator'))
    dispatch = store.dispatch_bound
    def changed_then_dispatch(*args, **kwargs):
        row = store.db.execute('SELECT data FROM artifacts WHERE id=?', (artifact.id,)).fetchone()
        data = json.loads(row['data']);data['result_hash'] = '0' * 64
        store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(data), artifact.id))
        return dispatch(*args, **kwargs)
    monkeypatch.setattr(store, 'dispatch_bound', changed_then_dispatch)
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='changed-metadata-before-claim')
    assert result.attempt is None and 'artifact_integrity' in result.reasons
    assert store.budget(owner, workflow.id) == before and len(store.attempts(owner, workflow.id)) == 2


def test_two_host_connections_cannot_duplicate_integration_assignment(integration_ready, owned):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.store import Store

    dispatcher, _, _, _, task = integration_ready
    store, owner, host, workflow, *_ = owned
    bind(integration_ready, owned)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-integrator'))
    barrier = Barrier(2)
    def tick(number):
        connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
        try:
            barrier.wait(timeout=5)
            return Dispatcher(connection, dispatcher.root).tick(host, key=f'integration-race-{number}')
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tick, range(2)))
    assert sum(result.attempt is not None for result in results) == 1
    attempts = [item for item in store.attempts(owner, workflow.id) if item.task_id == task.id]
    assert len(attempts) == 1 and attempts[0].state == 'succeeded'
    row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (attempts[0].id,)).fetchone()
    assert Integration(store, dispatcher.root).current(owner, row['id'])
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()


def test_dispatched_fresh_review_and_reintegration_revalidate_reopened_root(owned, delivery, completed):  # noqa: F811
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, author, producer, source = owned
    runtime, artifact, _, approval = delivery
    service, previous, previous_integrator = completed
    before = store.budget(owner, workflow.id)
    deadline = store.workflow(owner, workflow.id).deadline_at
    current = store.task(owner, author.id)
    opened = store.command(owner, current.id, TaskCommand(operation='reopen', expected_revision=current.revision,
        idempotency_key='reopen-before-dispatch'))
    store.command(owner, opened.id, TaskCommand(operation='enqueue', expected_revision=opened.revision,
        idempotency_key='enqueue-reopened-root'))
    store.start_next(owner, key='restart-before-dispatch')
    dispatcher = Dispatcher(store, runtime.root)
    def stage(title, tools):
        return store.create_task(owner, workflow.id, TaskDraft(title=title, kind='review',
            objective='Verify the current owner criteria using the exact retained artifact.', parent_id=author.id,
            acceptance=opened.acceptance, permissions=previous_integrator.profile_snapshot.permissions,
            budget=BudgetAmount(tool_calls=tools), input_artifacts={artifact.id: artifact.patch_hash}), key=title)
    task = stage('Fresh review for reopened root', 4)
    dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='bind-reopened-review')
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-reopened-review'))
    checked = dispatcher.tick(host, key='dispatch-reopened-review')
    assert checked.attempt is not None
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (checked.attempt.id,)).fetchone()
    review = Reviews(store, runtime.root).get(owner, row['id'])
    assert review.status == 'approved' and review.id not in previous.review_ids
    task = stage('Reintegrate reopened root', 16)
    binding = dispatcher.bind_integration(owner, task.id, (artifact.id,), (review.id,),
        approval_hashes={author.id: approval, task.id: approval}, expected_revision=task.revision,
        key='bind-reopened-integration', previous_result_id=previous.id)
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-reopened-integration'))
    result = dispatcher.tick(host, key='dispatch-reopened-integration')
    assert result.attempt is not None and result.attempt.execution_binding_id == binding.id
    row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (result.attempt.id,)).fetchone()
    renewed = service.get(owner, row['id'])
    assert renewed.state == 'completed' and service.current(owner, renewed.id)
    assert renewed.previous_result_id == previous.id and service.get(owner, previous.id).state == 'invalidated'
    assert renewed.artifact_hashes == previous.artifact_hashes and renewed.review_ids != previous.review_ids
    assert store._attempt(producer.id).state == 'succeeded'
    assert all(item.result_id == renewed.id for item in store.tasks(owner, workflow.id) if item.retired_by_result is None)
    after = store.budget(owner, workflow.id)
    assert after.spent.model_calls == before.spent.model_calls
    assert after.spent.tool_calls == before.spent.tool_calls + 20
    assert after.reserved == BudgetAmount() and not after.uncertain
    assert store.workflow(owner, workflow.id).deadline_at == deadline
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
