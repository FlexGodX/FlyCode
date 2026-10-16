"""Canonical completion requires an immutable, reviewed and verified repair."""

import json

import pytest

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
)
from flycode.domain.verification import CheckSpec


def repair(owned, model_server, tmp_path):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, _, _, attempt, source = owned
    (source / 'tests/test_example.py').write_text('import runpy\n'
        'def test_example():\n    assert runpy.run_path("src/example.py")["answer"] == 1\n')
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff user bytes\r\n')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'edit_file', 'arguments': {'path': 'src/example.py', 'old_text': '0', 'new_text': '1'}}]}))
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(15), handle.error
        assert handle.process_stopped.is_set() and runtime.worker(owner, attempt.id).stop_confirmed
        assert handle.error is None
    finally:
        handle.close()
    return runtime


def checks():
    return [CheckSpec(id=stage, stage=stage, expected_outcome='failed' if stage == 'baseline' else 'passed',
        targets=['tests/test_example.py::test_example'], dependencies=[] if stage == 'baseline' else [dependency])
        for stage, dependency in [('baseline', None), ('target', 'baseline'),
                                  ('regression', 'target'), ('integration', 'regression')]]


def reviewer(owned, artifacts, *, tools=24):
    store, owner, host, workflow, author, attempt, _ = owned
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    profile = store.register_profile(owner, AgentProfileDraft(role='reviewer',
        model_ref=attempt.profile_snapshot.model_ref, instructions='Verify the exact approved artifact.',
        project_ids=(author.project_id,), permissions=permission, context_limit=8192))
    task = store.create_task(owner, workflow.id, TaskDraft(title='Verify repair', kind='review',
        objective='Independently verify the exact repair with the approved oracle.', parent_id=author.id,
        acceptance=author.acceptance, permissions=permission, budget=BudgetAmount(tool_calls=tools),
        input_artifacts={artifact.id: artifact.patch_hash for artifact in artifacts}), key='review-task-' + str(len(store.tasks(owner, workflow.id))))
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-' + task.id))
    instance = store.register_instance(host, profile.id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key='claim-' + task.id))
    assert claim.attempt is not None, claim.reasons
    return store.confirm_started(host, claim.attempt.id, claim.attempt.fence)


@pytest.fixture
def delivery(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    store, owner, host, _, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    return runtime, artifact, review, approval


@pytest.fixture
def completed(owned, delivery):
    from flycode.orchestration.integration import Integration

    store, _, host, _, task, _, _ = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    service = Integration(store, runtime.root)
    result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    return service, result, integrator


def test_completed_result_is_current_across_metadata_archive_and_replay(owned, delivery, completed):
    store, owner, host, workflow, task, _, _ = owned
    service, result, integrator = completed
    _, artifact, review, approval = delivery
    before = store.budget(owner, workflow.id)
    assert service.current(owner, result.id)
    original = store.task(owner, task.id)
    metadata = store.command(owner, task.id, TaskCommand(operation='set_metadata', expected_revision=original.revision,
        idempotency_key='cosmetic-title', changes={'title': 'Reviewed repair', 'tags': ('checked',)}))
    assert metadata.semantic_revision == original.semantic_revision
    for hidden in (True, False):
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='archive', expected_revision=current.revision,
            idempotency_key='archive-'+str(hidden), changes={'archived': hidden}))
        assert service.current(owner, result.id)
    replay = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert replay.id == result.id and service.current(owner, replay.id)
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('change', ['source', 'untracked', 'criteria', 'evidence', 'patch', 'retained-original'])
def test_completed_result_changes_invalidate_without_refund_or_rewriting_history(owned, completed, change):
    from flycode.domain.verification import AcceptanceContract

    store, owner, _, workflow, task, author, source = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    history = store.attempts(owner, workflow.id)
    if change == 'source':
        (source / 'src/example.py').write_bytes(b'user changed source\n')
    elif change == 'untracked':
        (source / 'new-user-file.bin').write_bytes(b'new user bytes')
    elif change == 'criteria':
        current = store.task(owner, task.id)
        acceptance = AcceptanceContract.model_validate({**current.acceptance.model_dump(), 'version': 2})
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='revise-criteria', changes={'acceptance': acceptance.model_dump()}))
        assert service.get(owner, result.id).state == 'invalidated'
    elif change == 'evidence':
        verdict = result.applied_verdicts[task.id]
        (service.engine.data_dir / verdict.evidence_id / 'manifest.json').write_bytes(b'changed evidence')
    elif change == 'patch':
        artifact_id = next(iter(result.artifact_hashes))
        (service.artifacts.directory / artifact_id / 'target/src/example.py').write_bytes(b'changed patch')
    else:
        (service.root / 'integrations' / result.id / 'recovery/0').write_bytes(b'late user bytes\n')
    assert not service.current(owner, result.id)
    invalidated = service.refresh(owner, result.id)
    assert invalidated.state == 'invalidated'
    assert store.task(owner, task.id).state == 'blocked'
    assert store.task(owner, task.id).current_attempt_id is None
    assert store.workflow(owner, workflow.id).state == 'incomplete'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.budget(owner, workflow.id) == before
    assert store.attempts(owner, workflow.id) == history
    assert store._attempt(author.id).state == 'succeeded'
    assert any(event.kind == 'result_invalidated' for event in store.events(owner, workflow.id))
    assert service.refresh(owner, result.id) == invalidated
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_tracker_refreshes_stale_result_without_model_calls(owned, completed, model_server):
    from flycode.domain.orchestration import AccessScope
    from flycode.tracker.service import TrackerService

    store, _, _, workflow, task, _, source = owned
    _, result, _ = completed
    reader = AccessScope(actor_id='reader', project_ids={'project'}, role='reader')
    before = len(model_server.requests), store.budget(reader, workflow.id)
    (source / 'src/example.py').write_bytes(b'new user bytes\n')
    tracker = TrackerService(store)
    card = tracker.task(reader, task.id)
    assert card.column == 'blocked' and 'result_invalidated' in card.blockers
    assert card.task.result_id == result.id
    assert card.attempt.state == 'succeeded'
    assert all(card.column == 'blocked' for card in tracker.board(reader, workflow.id))
    assert (len(model_server.requests), store.budget(reader, workflow.id)) == before


def test_replay_with_changed_policy_never_reexecutes_completed_integration(owned, delivery, completed):
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, _ = owned
    _, artifact, review, approval = delivery
    service, result, integrator = completed
    before = store.budget(owner, workflow.id)
    for arguments in ({'reruns': 2}, {'approval_hashes': {task.id: approval}}, {'fence': integrator.fence + 1}):
        kwargs = {'approval_hashes': {task.id: approval, integrator.task_id: approval}, **arguments}
        fence = kwargs.pop('fence', integrator.fence)
        with pytest.raises(StoreError, match='idempotency'):
            service.run(host, integrator.id, fence, (artifact.id,), (review.id,), **kwargs)
    assert service.current(owner, result.id)
    assert store.budget(owner, workflow.id) == before


def test_result_inspection_cannot_hold_sql_transaction_or_cross_scope(owned, completed):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    store, owner, _, _, _, _, _ = owned
    service, result, _ = completed
    foreign = AccessScope(actor_id='foreign', project_ids={'other'}, role='owner')
    with pytest.raises(StoreError, match='scope'):
        service.refresh(foreign, result.id)
    with store.transaction():
        with pytest.raises(RuntimeError, match='transaction'):
            service.current(owner, result.id)
        with pytest.raises(RuntimeError, match='transaction'):
            store.refresh_results(owner)
    assert service.current(owner, result.id)


def test_reopen_retires_exact_old_checks_and_claims_same_task_with_remaining_budget(owned, completed):
    from flycode.orchestration.store import StoreError
    from flycode.tracker.service import TrackerService

    store, owner, host, workflow, task, author, _ = owned
    service, result, _ = completed
    original = store.task(owner, task.id)
    before = store.budget(owner, workflow.id)
    times = store.workflow(owner, workflow.id)
    attempts = store.attempts(owner, workflow.id)
    command = TaskCommand(operation='reopen', expected_revision=original.revision, idempotency_key='reopen-result')
    opened = store.command(owner, task.id, command)
    assert opened.id == task.id and opened.state == 'planned'
    assert opened.semantic_revision == original.semantic_revision + 1
    assert opened.result_id is None
    assert service.get(owner, result.id).state == 'invalidated' and not service.current(owner, result.id)
    assert store.workflow(owner, workflow.id).state == 'draft'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.workflow(owner, workflow.id).started_at == times.started_at
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at
    assert store.attempts(owner, workflow.id) == attempts
    assert store.budget(owner, workflow.id) == before
    assert store.command(owner, task.id, command) == opened
    historical = [item for item in store.tasks(owner, workflow.id) if item.id != task.id]
    assert len(historical) == 2
    assert all(item.retired_by_result == result.id and item.archived for item in historical)
    tracker = TrackerService(store)
    assert [card.task.id for card in tracker.board(owner, workflow.id)] == [task.id]
    assert len(tracker.board(owner, workflow.id, archived=True)) == 3
    for old in historical:
        with pytest.raises(StoreError, match='historical_result'):
            store.command(owner, old.id, TaskCommand(operation='reopen', expected_revision=old.revision,
                idempotency_key='reopen-old-'+old.id))
    ready = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='reenqueue'))
    assert store.start_next(owner, key='restart').id == workflow.id
    instance = store.instance(host, author.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=ready.revision, reserve=BudgetAmount(tool_calls=1), idempotency_key='reclaim'))
    assert claim.attempt is not None, claim.reasons
    assert claim.attempt.task_id == author.task_id and claim.attempt.id != author.id
    assert store.budget(owner, workflow.id).spent == before.spent
    assert len([attempt for attempt in store.attempts(owner, workflow.id) if attempt.task_id == task.id]) == 2


def test_root_reopen_preserves_owner_revised_review_generation(owned, delivery, completed):
    store, owner, _, _, task, _, source = owned
    service, result, _ = completed
    _, _, review, _ = delivery
    (source / 'owner-note.txt').write_text('New owner requirement\n')
    service.refresh(owner, result.id)
    checker = store.task(owner, store._attempt(review.reviewer_attempt_id).task_id)
    revised = store.command(owner, checker.id, TaskCommand(operation='revise', expected_revision=checker.revision,
        idempotency_key='revise-old-review', changes={'objective': 'Verify the new owner requirement as well.'}))
    root = store.task(owner, task.id)
    store.command(owner, root.id, TaskCommand(operation='reopen', expected_revision=root.revision, idempotency_key='reopen-with-new-review'))
    current = store.task(owner, checker.id)
    assert current.retired_by_result is None and not current.archived
    assert current.semantic_revision == revised.semantic_revision
    assert current.objective == revised.objective


@pytest.mark.parametrize('source_changed', [False, True])
def test_reopen_selected_review_requires_fresh_checks_without_reapplying_patch(owned, delivery, completed, source_changed):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, author, source = owned
    service, result, _ = completed
    runtime, artifact, review, approval = delivery
    author = store._attempt(author.id)
    checker = store._attempt(review.reviewer_attempt_id)
    old = store.task(owner, checker.task_id)
    opened = store.command(owner, old.id, TaskCommand(operation='reopen', expected_revision=old.revision, idempotency_key='reopen-checker'))
    assert opened.retired_by_result is None and not opened.archived
    assert opened.semantic_revision == old.semantic_revision + 1
    assert opened.state == 'planned' and opened.result_id is None
    assert store._attempt(checker.id) == checker
    assert store.task(owner, task.id).state == 'planned'
    ready = store.command(owner, opened.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-checker-again'))
    root = store.task(owner, task.id)
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=root.revision, idempotency_key='enqueue-aggregate'))
    assert store.start_next(owner, key='restart-checker').id == workflow.id
    instance = store.instance(host, checker.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=ready.id, expected_task_revision=ready.revision, reserve=BudgetAmount(tool_calls=4), idempotency_key='claim-checker-again'))
    assert claim.attempt is not None, claim.reasons
    assert claim.attempt.task_id == old.id and claim.attempt.id != checker.id
    assert all(item.retired_by_result == result.id for item in store.tasks(owner, workflow.id)
        if item.id not in {task.id, old.id})
    second = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    fresh = Reviews(store, runtime.root).run(host, second.id, second.fence, artifact.id, approval_hash=approval)
    assert fresh.status == 'approved' and fresh.id != review.id
    integrator = reviewer(owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    installed = (source / 'src/example.py').stat().st_ino
    if source_changed:
        (source / 'owner-change.txt').write_text('Preserve this change\n')
        with pytest.raises(StoreError, match='integration_conflict'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert (source / 'owner-change.txt').read_text() == 'Preserve this change\n'
        assert store.budget(owner, workflow.id).spent == before
    else:
        with pytest.raises(StoreError, match='review_changed'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert store.budget(owner, workflow.id).spent == before
        renewed = service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert renewed.id != result.id and renewed.previous_result_id == result.id
        assert service.current(owner, renewed.id) and not service.current(owner, result.id)
        assert service.get(owner, result.id).state == 'invalidated'
        assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls
        assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 16
        assert store.task(owner, old.id).state == 'succeeded'
        assert store.task(owner, task.id).result_id == renewed.id
        replay = service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert replay.model_dump(mode='json') == renewed.model_dump(mode='json')
        assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 16
        with pytest.raises(StoreError, match='idempotency'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        original = runtime.root / 'integrations' / result.id / 'recovery/0'
        original.write_bytes(b'late owner write to the retained original\n')
        assert service.refresh(owner, renewed.id).state == 'invalidated'
        assert not service.current(owner, renewed.id)
        assert original.read_bytes() == b'late owner write to the retained original\n'
    assert (source / 'src/example.py').stat().st_ino == installed
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert store._attempt(author.id) == author
    assert store._attempt(checker.id) == checker


@pytest.mark.parametrize('operation', ['cancel', 'pause'])
def test_reverification_preserves_owner_stop_of_historical_child(owned, model_server, tmp_path, monkeypatch, operation):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, root, original, source = owned
    store.confirm_stopped(host, original.id, original.fence, outcome='retry_wait', expenditure_confirmed=True)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key='child-for-reverify')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.instance(host, original.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    runtime = repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    service = Integration(store, runtime.root)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = service.engine.oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact], tools=32)
    result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    current = store.task(owner, root.id)
    opened = store.command(owner, root.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-root'))
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-root'))
    assert store.start_next(owner, key='restart-root').id == workflow.id
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact], tools=32)

    def stop_child():
        current = store.task(owner, child.id)
        store.command(owner, child.id, TaskCommand(operation=operation, expected_revision=current.revision, idempotency_key='stop-child'))

    if operation == 'cancel':
        stop_child()
    else:
        verify = service._verify

        def verify_then_pause(*args, **kwargs):
            record = verify(*args, **kwargs)
            if not kwargs['applied']:
                stop_child()
            return record
        monkeypatch.setattr(service, '_verify', verify_then_pause)
    historical = store._attempt(author.id)
    with pytest.raises(StoreError, match='completion_blocked'):
        service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
    assert store.task(owner, child.id).state != 'succeeded'
    assert store.task(owner, child.id).paused is (operation == 'pause')
    assert store._attempt(author.id) == historical
    assert store.workflow(owner, workflow.id).state != 'succeeded'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'


def test_expired_reopen_cannot_capture_the_only_workflow_slot(owned, completed):
    store, owner, _, workflow, task, _, _ = owned
    times = store.workflow(owner, workflow.id)
    store.clock = lambda: times.deadline_at + 1
    current = store.task(owner, task.id)
    opened = store.command(owner, task.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-expired'))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-expired'))
    assert store.start_next(owner, key='start-expired') is None
    assert store.workflow(owner, workflow.id).state == 'incomplete'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at


def test_cancel_completed_result_preserves_owner_cancellation(owned, completed):
    store, owner, _, workflow, task, _, _ = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    current = store.task(owner, task.id)
    store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision, idempotency_key='cancel-completed'))
    assert service.get(owner, result.id).state == 'invalidated'
    assert store.workflow(owner, workflow.id).state == 'cancelled'
    assert store.task(owner, task.id).state == 'cancelled'
    assert store.budget(owner, workflow.id) == before


def test_reopened_generation_requires_new_real_checks_and_preserves_prior_result(owned, delivery, completed, model_server):
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, task, author, source = owned
    runtime, _, _, _ = delivery
    service, old_result, _ = completed
    before = store.budget(owner, workflow.id).spent
    times = store.workflow(owner, workflow.id)
    current = store.task(owner, task.id)
    current = store.command(owner, task.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-second-generation'))
    acceptance = {**current.acceptance.model_dump(), 'version': 2}
    current = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
        idempotency_key='owner-extends-task-budget', changes={'acceptance': acceptance, 'budget': {'model_calls': 4, 'tool_calls': 6}}))
    current = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=current.revision, idempotency_key='enqueue-second-generation'))
    store.start_next(owner, key='start-second-generation')
    instance = store.instance(host, author.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=current.revision, reserve=BudgetAmount(model_calls=1, tool_calls=1), idempotency_key='claim-second-generation'))
    assert claim.attempt is not None, claim.reasons
    second = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}]}))
    handle = runtime.launch(host, second.id, second.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(15), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
    finally:
        handle.close()
    artifact = Artifacts(store, runtime.root).for_attempt(owner, second.id)
    assert artifact.changes == ()
    assert not service.current(owner, old_result.id)
    revised = store.task(owner, task.id)
    new_owned = store, owner, host, workflow, revised, second, source
    # The already repaired baseline now must pass; its owner-approved policy
    # belongs to acceptance version2 and cannot reuse the old green verdict.
    # Use the unchanged fixture oracle retained by the delivery fixture.
    oracle = runtime.root.parent / 'owner-oracle'
    specs = [check.model_copy(update={'expected_outcome': 'passed'}) for check in checks()]
    approval = service.engine.oracles.approve(revised.acceptance, specs, oracle)
    assert approval != old_result.approval_hashes[task.id]
    checker = reviewer(new_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(new_owned, [artifact])
    new_result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert new_result.id != old_result.id and service.current(owner, new_result.id)
    assert service.get(owner, old_result.id).state == 'invalidated'
    assert store.task(owner, task.id).result_id == new_result.id
    assert store._attempt(author.id).state == store._attempt(second.id).state == 'succeeded'
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at
    assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls + 1
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 22
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_two_connections_invalidate_once_and_restart_retains_fact(owned, completed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import Store
    from flycode.tracker.service import TrackerService

    store, owner, _, workflow, task, _, source = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    (source / 'src/example.py').write_bytes(b'user concurrent update\n')
    barrier = Barrier(2)

    def refresh():
        other = Store(store.path)
        try:
            checker = Integration(other, service.root)
            barrier.wait(timeout=5)
            return checker.refresh(owner, result.id)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh(), range(2)))
    assert all(record.state == 'invalidated' for record in results)
    events = [event for event in store.events(owner, workflow.id) if event.kind == 'result_invalidated']
    assert len(events) == len(result.contracts)
    restarted = Store(store.path)
    try:
        card = TrackerService(restarted).task(owner, task.id)
        assert card.column == 'blocked' and 'result_invalidated' in card.blockers
        assert restarted.budget(owner, workflow.id) == before
        assert len([event for event in restarted.events(owner, workflow.id) if event.kind == 'result_invalidated']) == len(events)
    finally:
        restarted.close()


def test_invalidating_old_result_never_releases_another_active_workflow(owned, completed):
    from flycode.domain.orchestration import WorkflowDraft

    store, owner, _, workflow, task, _, source = owned
    service, result, _ = completed
    other = store.create_workflow(owner, WorkflowDraft(project_id=task.project_id, task=TaskDraft(
        title='Next explicit task', objective=task.objective, acceptance=task.acceptance, permissions=task.permissions,
        budget=task.budget), budget=workflow.budget), key='other-workflow')
    root = store.task(owner, other.root_task_id)
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=root.revision, idempotency_key='enqueue-other'))
    assert store.start_next(owner, key='start-other').id == other.id
    before = store.workflow(owner, other.id)
    (source / 'src/example.py').write_bytes(b'user changed old project\n')
    assert service.refresh(owner, result.id).state == 'invalidated'
    assert store.workflow(owner, other.id) == before
    assert store.workflow(owner, other.id).active_slot
    assert not store.workflow(owner, workflow.id).active_slot


def test_claim_refreshes_stale_result_before_revision_or_admission(owned, completed):
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, author, source = owned
    _, result, _ = completed
    previous = store.task(owner, task.id)
    instance = store.instance(host, author.instance_id)
    (source / 'src/example.py').write_bytes(b'user changed dependency\n')
    with pytest.raises(StoreError, match='revision'):
        store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=task.id, expected_task_revision=previous.revision, reserve=BudgetAmount(tool_calls=1), idempotency_key='stale-result-claim'))
    assert store.task(owner, task.id).state == 'blocked'
    assert store.task(owner, task.id).result_id == result.id
    assert not store.workflow(owner, workflow.id).active_slot


def test_schema8_result_without_freshness_pins_invalidates_after_restart(owned, completed):
    from flycode.orchestration.store import Store
    from flycode.tracker.service import TrackerService

    store, owner, _, workflow, task, _, _ = owned
    _, result, _ = completed
    before = store.budget(owner, workflow.id)
    attempts = store.attempts(owner, workflow.id)
    legacy = result.model_dump(mode='json', exclude={
        'runtime_root', 'previous_result_id', 'semantic_revisions', 'approval_hashes', 'reruns'})
    store.db.execute('UPDATE integrations SET data=? WHERE id=?', (json.dumps(legacy), result.id))
    store.db.execute('DROP INDEX current_integration')
    store.db.execute('PRAGMA user_version=8')
    restarted = Store(store.path, catalogue=store.catalogue)
    try:
        card = TrackerService(restarted).task(owner, task.id)
        assert card.task.state == 'blocked' and card.task.result_id == result.id
        assert restarted.workflow(owner, workflow.id).state == 'incomplete'
        assert not restarted.workflow(owner, workflow.id).active_slot
        assert restarted.budget(owner, workflow.id) == before
        assert restarted.attempts(owner, workflow.id) == attempts
        assert json.loads(restarted.db.execute('SELECT data FROM integrations WHERE id=?', (result.id,)).fetchone()['data'])['state'] == 'invalidated'
    finally:
        restarted.close()


@pytest.mark.parametrize('field', ['result_id', 'retired_by_result', 'blocked_reason'])
def test_owner_command_cannot_supply_host_result_fields(owned, field):
    from flycode.orchestration.store import StoreError

    store, owner, _, _, task, _, _ = owned
    current = store.task(owner, task.id)
    with pytest.raises(StoreError, match='command'):
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='forge-'+field, changes={field: 'forged-result'}))
    assert store.task(owner, task.id) == current


@pytest.mark.parametrize('operation', ['pause', 'cancel', 'release_assignment'])
def test_published_assignment_can_stop_without_touching_a_reused_instance(owned, model_server, tmp_path, operation):
    from flycode.orchestration.artifacts import Artifacts

    store, owner, host, workflow, root, root_attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)

    def child(key, instance=None):
        task = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
            parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key=key)
        task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key=key+'-enqueue'))
        instance = instance or store.register_instance(host, root_attempt.profile_id)
        result = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key=key+'-claim'))
        assert result.attempt is not None, result.reasons
        return task, store.confirm_started(host, result.attempt.id, result.attempt.fence)

    task, author = child('author')
    repair((store, owner, host, workflow, task, author, source), model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    _, replacement = child('replacement', store.instance(host, author.instance_id))
    instance = store.instance(host, author.instance_id)
    before = store.budget(owner, workflow.id).spent
    current = store.task(owner, task.id)
    command = TaskCommand(operation=operation, expected_revision=current.revision, idempotency_key='stop-published')
    result = store.command(owner, current.id, command)
    assert result.current_attempt_id is None
    assert result.state == ('cancelled' if operation == 'cancel' else 'retry_wait')
    assert result.paused is (operation == 'pause')
    assert store._attempt(author.id).state == ('cancelled' if operation == 'cancel' else 'interrupted')
    assert store._attempt(author.id).stop_confirmed
    assert store.instance(host, author.instance_id) == instance
    assert store.instance(host, author.instance_id).current_attempt_id == replacement.id
    assert store.budget(owner, workflow.id).spent == before
    assert Artifacts(store, runtime.root).get(owner, artifact.id) == artifact
    assert store.command(owner, current.id, command) == result


def test_late_write_to_displaced_original_blocks_canonical_success(owned, delivery, monkeypatch):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    integration = Integration(store, runtime.root)
    verify = integration._verify
    with (source / 'src/example.py').open('r+b') as original:
        def verify_then_edit(*args, **kwargs):
            record = verify(*args, **kwargs)
            if kwargs['applied']:
                original.write(b'user late bytes\n')
                original.truncate()
                original.flush()
            return record
        monkeypatch.setattr(integration, '_verify', verify_then_edit)
        with pytest.raises(StoreError, match='integration_conflict'):
            integration.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
    assert store.workflow(owner, workflow.id).state != 'succeeded'
    assert store.task(owner, task.id).state != 'succeeded'
    assert (source / 'src/example.py').read_bytes() == b'user late bytes\n'


def test_integrator_cannot_share_an_active_worker(owned, delivery):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    # A reviewer worker performs real registered tests even without model budget.
    handle = runtime.launch(host, integrator.id, integrator.fence, source, brain='heuristic_fsm')
    try:
        with pytest.raises(StoreError, match='worker_exists'):
            Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        assert not any(call.tool == 'integrate' for call in Tools(store).calls(owner, workflow.id))
        assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    finally:
        handle.close()


def test_runtime_cannot_start_inside_an_admitted_host_review(owned, delivery, monkeypatch):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, _, host, _, _, _, source = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    reviews = Reviews(store, runtime.root)
    from flycode.verification import VerificationEngine
    verify = VerificationEngine.verify_plan
    blocked = []

    def verify_with_competing_worker(engine, *args, **kwargs):
        try:
            handle = runtime.launch(host, checker.id, checker.fence, source, brain='heuristic_fsm')
        except StoreError as error:
            assert error.code == 'execution_busy'
            blocked.append(True)
        else:
            handle.close()
            pytest.fail('A worker started while host verification owned the attempt')
        return verify(engine, *args, **kwargs)

    monkeypatch.setattr(VerificationEngine, 'verify_plan', verify_with_competing_worker)
    result = reviews.run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'approved' and blocked == [True]


def test_live_root_assignment_blocks_integration_of_child_patch(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, root, root_attempt, source = owned
    child_permissions = root.permissions
    store.confirm_stopped(host, root_attempt.id, root_attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
    root = store.task(owner, root.id)
    root = store.command(owner, root.id, TaskCommand(operation='revise', expected_revision=root.revision,
        idempotency_key='read-only-root', changes={'permissions': ExecutionPermission(approved=True,
            read_scopes=('.',), tools=('read_file', 'run_tests')).model_dump()}))
    root = store.command(owner, root.id, TaskCommand(operation='retry', expected_revision=root.revision, idempotency_key='retry-root'))
    root_instance = store.instance(host, root_attempt.instance_id)
    root_claim = store.claim(host, ClaimRequest(instance_id=root_instance.id, expected_instance_revision=root_instance.revision,
        task_id=root.id, expected_task_revision=root.revision, reserve=root.budget, idempotency_key='reclaim-root'))
    assert root_claim.attempt is not None, root_claim.reasons
    root_attempt = store.confirm_started(host, root_claim.attempt.id, root_claim.attempt.fence)
    root = store.task(owner, root.id)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=child_permissions, budget=root.budget), key='child-author')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.register_instance(host, root_attempt.profile_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    runtime = repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='incomplete_workflow'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert store.task(owner, root.id).current_attempt_id == root_attempt.id
    assert store._attempt(root_attempt.id).state == 'running'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_unselected_published_root_cannot_be_completed_by_child_patch(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, root, root_attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key='child-author')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.register_instance(host, root_attempt.profile_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='incomplete_workflow'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert store.task(owner, root.id).state == 'awaiting_review'
    assert store.task(owner, root.id).current_attempt_id == root_attempt.id
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('tamper', ['manifest', 'blob', 'mode', 'worker-result'])
def test_tampered_patch_cannot_enter_review_or_spend(owned, delivery, tamper):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    directory = runtime.root / 'artifacts' / artifact.id
    if tamper == 'mode':
        (directory / 'target/src/example.py').chmod(0o755)
    else:
        name = {'manifest': 'manifest.json', 'blob': 'target/src/example.py', 'worker-result': 'run-result.json'}[tamper]
        with (directory / name).open('ab') as stream:
            stream.write(b' altered bytes')
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='artifact_integrity'):
        Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert store.budget(owner, workflow.id).spent == before
    assert not any(call.attempt_id == checker.id for call in Tools(store).calls(owner, workflow.id))


def test_review_replay_rejects_changed_rerun_policy_without_spending(owned, delivery):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, review, approval = delivery
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='idempotency'):
        Reviews(store, runtime.root).run(host, review.reviewer_attempt_id, review.reviewer_fence,
            artifact.id, approval_hash=approval, reruns=2)
    assert store.budget(owner, workflow.id).spent == before


def test_review_budget_cannot_approve_unexecuted_registered_checks(owned, delivery):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact], tools=2)
    before = store.budget(owner, workflow.id).spent
    result = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'inconclusive'
    assert result.verdict.status != 'passed'
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 2
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 2
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert store.task(owner, checker.task_id).state == 'failed'


def test_owner_cancel_after_checks_retains_evidence_and_settles_review(owned, delivery, monkeypatch):
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    verify = VerificationEngine.verify_plan
    before = store.budget(owner, workflow.id).spent

    def cancel_after_checks(engine, *args, **kwargs):
        verdict = verify(engine, *args, **kwargs)
        assert verdict.status == 'passed'
        task = store.task(owner, checker.task_id)
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=task.revision,
            idempotency_key='cancel-review-at-boundary'))
        return verdict

    monkeypatch.setattr(VerificationEngine, 'verify_plan', cancel_after_checks)
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'inconclusive' and review.verdict.status == 'passed'
    assert store.task(owner, checker.task_id).state == 'cancelled'
    assert store.task(owner, checker.task_id).current_attempt_id is None
    assert store._attempt(checker.id).state == 'cancelled' and store._attempt(checker.id).stop_confirmed
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 4
    assert (runtime.root / 'verification' / review.verdict.evidence_id / 'manifest.json').exists()


def test_changed_source_is_preserved_and_blocks_integration_before_checks(owned, delivery):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    (source / 'src/example.py').write_bytes(b'user changed source\n')
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='integration_conflict'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert (source / 'src/example.py').read_bytes() == b'user changed source\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert store.workflow(owner, workflow.id).state != 'succeeded'


def test_actual_stopped_worker_publishes_exact_patch_without_touching_user_source(owned, model_server, tmp_path):
    from flycode.orchestration.artifacts import Artifacts

    store, owner, _, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    assert artifact.attempt_id == attempt.id and artifact.contract_hash == attempt.contract_hash
    assert [change.path for change in artifact.changes] == ['src/example.py']
    assert artifact.base.files['src/example.py'] != artifact.target.files['src/example.py']
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert store._attempt(attempt.id).stop_confirmed
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert any(event.kind == 'artifact_published' for event in store.events(owner, workflow.id))


def test_independent_review_executes_and_charges_each_real_registered_check(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    checker = reviewer(owned, [artifact])
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    before = store.budget(owner, workflow.id).spent
    result = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'approved' and result.patch_hash == artifact.patch_hash
    assert result.verdict.status == 'passed' and result.verdict.required_coverage == 1
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 4 and all(call.tool == 'run_tests' and call.status == 'completed' for call in calls)
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 4
    assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls
    assert store.task(owner, checker.task_id).state == 'succeeded'
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_real_repair_review_integration_application_and_current_verified_completion(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(owned, [artifact])
    result = Integration(store, runtime.root).run(host, integrator.id, integrator.fence,
        (artifact.id,), (review.id,), approval_hashes={task.id: approval, integrator.task_id: approval})
    assert result.state == 'completed'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert engine.current(result.applied_verdicts[task.id], source, contract=task.acceptance)
    assert store.task(owner, task.id).state == 'succeeded'
    assert store.task(owner, integrator.task_id).state == 'succeeded'
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store._attempt(attempt.id).stop_confirmed
    calls = Tools(store).calls(owner, workflow.id)
    assert len([call for call in calls if call.attempt_id == integrator.id]) == 17
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
