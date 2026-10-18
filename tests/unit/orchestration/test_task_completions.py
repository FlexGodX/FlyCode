"""A verified task can finish before the workflow without a self-reported pass."""

import importlib.util

import pytest
from test_pipeline_dispatch import pipeline_ready, published_pipeline  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import ClaimRequest, TaskCommand, TaskDraft
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.review import Reviews
from flycode.orchestration.store import StoreError, changed


@pytest.fixture
def approved_task(published_pipeline):  # noqa: F811
    prepared, _ = published_pipeline
    store, owner, host, workflow, _, author, reviewer, _, dispatcher, source, _ = prepared
    selected = dispatcher.tick(host, key='complete-independent-review')
    assert selected.attempt is not None and selected.attempt.task_id == reviewer.id
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (selected.attempt.id,)).fetchone()
    review = Reviews(store, dispatcher.root).get(owner, row['id'])
    assert review.status == 'approved'
    assert store.task(owner, author.id).state == 'awaiting_review'
    assert store.workflow(owner, workflow.id).state == 'running'
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    return prepared, review


def service(prepared):
    assert importlib.util.find_spec('flycode.orchestration.completions') is not None, 'Verified task-completion gate is missing'
    from flycode.orchestration.completions import Completions

    return Completions(prepared[0], prepared[8].root)


def finish_author(prepared, review, *, key='finish-author'):
    store, _, host, _, _, author, _, _, dispatcher, *_ = prepared
    artifact = Artifacts(store, dispatcher.root).for_attempt(host, store.task(host, author.id).current_attempt_id)
    return service(prepared).complete(host, artifact.id, review.id,
        expected_revision=store.task(host, author.id).revision, key=key)


@pytest.mark.parametrize('mutation', ['missing-worker', 'self-review', 'rejected', 'review-contract', 'worker-hash'])
def test_completion_rejects_changed_author_or_independent_review(approved_task, mutation):
    prepared, review = approved_task
    store, owner, _, workflow, _, author, _, _, dispatcher, *_ = prepared
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    before = store.budget(owner, workflow.id)
    if mutation == 'missing-worker':
        store.db.execute('DELETE FROM workers WHERE attempt_id=?', (artifact.attempt_id,))
    elif mutation in {'self-review', 'review-contract'}:
        checker = store._attempt(review.reviewer_attempt_id)
        checker = changed(checker, **({'instance_id': store._attempt(artifact.attempt_id).instance_id}
            if mutation == 'self-review' else {'contract_hash': 'changed'}))
        with store.transaction():
            store._save_attempt(checker)
    elif mutation == 'rejected':
        store.db.execute('UPDATE reviews SET data=? WHERE id=?', (changed(review, status='changes_requested').model_dump_json(), review.id))
    else:
        import json

        row = store.db.execute('SELECT id,data FROM workers WHERE attempt_id=?', (artifact.attempt_id,)).fetchone()
        store.db.execute('UPDATE workers SET data=? WHERE id=?', (json.dumps({**json.loads(row['data']), 'result_hash': '0' * 64}), row['id']))
    with pytest.raises(StoreError):
        finish_author(prepared, review)
    assert store.task(owner, author.id).state == 'awaiting_review'
    assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 0
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('mutation', ['source', 'artifact', 'evidence', 'criteria', 'reviewer-criteria', 'reopen', 'permission'])
def test_stale_completion_is_blocked_before_dependent_admission(approved_task, mutation):
    prepared, review = approved_task
    store, owner, _, workflow, _, author, reviewer, _, dispatcher, source, _ = prepared
    proof = finish_author(prepared, review)
    assert service(prepared).current(owner, proof.id)
    current = store.task(owner, author.id)
    if mutation == 'source':
        (source / 'dirty.bin').write_bytes(b'owner changed this')
    elif mutation == 'artifact':
        (Artifacts(store, dispatcher.root).directory / proof.artifact_id / 'target/src/parser.py').write_text('value = 999\n')
    elif mutation == 'evidence':
        (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
    else:
        if mutation == 'reviewer-criteria':
            current = store.task(owner, reviewer.id)
        operation = 'reopen' if mutation == 'reopen' else 'revise'
        changes = {} if mutation == 'reopen' else {'permissions': changed(current.permissions, approved=False).model_dump()} if mutation == 'permission' else {
            'objective': 'An explicitly changed owner criterion.'}
        if mutation == 'permission':
            # Revocation is a Host operation; public revise requires approved enqueue authority.
            with store.transaction():
                store._save_task(changed(current, permissions=changed(current.permissions, approved=False)))
        else:
            store.command(owner, current.id, TaskCommand(operation=operation, expected_revision=current.revision,
                idempotency_key='change-completed-task', changes=changes))
    assert not service(prepared).current(owner, proof.id)
    store.refresh_results(owner, workflow.id)
    assert store.task(owner, author.id).state != 'succeeded'
    assert store.task(owner, author.id).completion_id == (None if mutation == 'reopen' else proof.id)
    assert store.workflow(owner, workflow.id).state != 'succeeded'


@pytest.mark.parametrize('operation,changes', [('set_metadata', {'title': 'A clearer title'}), ('archive', {})])
def test_metadata_and_archive_preserve_task_completion(approved_task, operation, changes):
    prepared, review = approved_task
    store, owner, _, workflow, _, author, *_ = prepared
    proof = finish_author(prepared, review)
    current = store.task(owner, author.id)
    store.command(owner, author.id, TaskCommand(operation=operation, expected_revision=current.revision,
        idempotency_key='metadata-after-completion', changes=changes))
    store.refresh_results(owner, workflow.id)
    assert service(prepared).current(owner, proof.id)
    assert store.task(owner, author.id).state == 'succeeded'


def test_exact_review_completes_only_author_without_applying_or_finishing_workflow(approved_task, model_server):
    prepared, review = approved_task
    store, owner, host, workflow, _, author, _, _, dispatcher, source, _ = prepared
    completion = service(prepared)
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    before = store.budget(owner, workflow.id), len(model_server.requests)
    revision = store.task(owner, author.id).revision
    proof = completion.complete(host, artifact.id, review.id, expected_revision=revision, key='complete-author')
    assert completion.complete(host, artifact.id, review.id, expected_revision=revision, key='complete-author') == proof
    current = store.task(owner, author.id)
    assert current.state == 'succeeded' and current.current_attempt_id is None
    assert current.completion_id == proof.id and current.result_id is None
    assert proof.artifact_id == artifact.id and proof.review_id == review.id and proof.prerequisites == {}
    assert completion.current(owner, proof.id)
    assert store._attempt(artifact.attempt_id).state == 'succeeded'
    assert store.workflow(owner, workflow.id).state == 'running'
    assert store.task(owner, workflow.root_task_id).state != 'succeeded'
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert (store.budget(owner, workflow.id), len(model_server.requests)) == before
    assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 1


def test_declared_dependency_remains_blocked_until_verified_task_completion(approved_task):
    prepared, review = approved_task
    store, owner, host, workflow, root, author, _, _, dispatcher, _, _ = prepared
    dependent = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Dependent implementation',
        'parent_id': root.id, 'dependencies': (author.id,)}), key='dependent-task')
    dependent = store.command(owner, dependent.id, TaskCommand(operation='enqueue', expected_revision=dependent.revision,
        idempotency_key='enqueue-dependent'))
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    request = {'instance_id': instance.id, 'expected_instance_revision': instance.revision,
        'task_id': dependent.id, 'expected_task_revision': dependent.revision, 'reserve': dependent.budget}
    before = store.claim(host, ClaimRequest(**request, idempotency_key='before-task-completion'))
    assert before.attempt is None and 'dependency' in before.reasons
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    proof = service(prepared).complete(host, artifact.id, review.id,
        expected_revision=store.task(owner, author.id).revision, key='complete-prerequisite')
    assert store.task(owner, author.id).completion_id == proof.id
    after = store.claim(host, ClaimRequest(**request, idempotency_key='after-task-completion'))
    assert after.attempt is not None, after.reasons
    store.confirm_stopped(host, after.attempt.id, after.attempt.fence, outcome='cancelled', expenditure_confirmed=True)
    assert store.workflow(owner, workflow.id).state == 'running'


@pytest.mark.parametrize('mutation', ['pause', 'queue-pause', 'cancel', 'deadline', 'clock-rollback', 'execution-approval', 'execution-artifact'])
def test_completion_rechecks_execution_and_stop_authority(approved_task, mutation):
    from flycode.domain.results import ReviewExecution

    prepared, review = approved_task
    store, owner, _, workflow, root, author, *_ = prepared
    before = store.budget(owner, workflow.id)
    if mutation in {'execution-approval', 'execution-artifact'}:
        execution = store._load('review_executions', review.reviewer_attempt_id, ReviewExecution)
        execution = changed(execution, **({'approval_hash': '0' * 64} if mutation == 'execution-approval' else {'artifact_id': 'wrong'}))
        store.db.execute('UPDATE review_executions SET data=? WHERE id=?', (execution.model_dump_json(), execution.attempt_id))
    elif mutation == 'queue-pause':
        store.db.execute("UPDATE counters SET value=1 WHERE name='queue_paused'")
    elif mutation in {'deadline', 'clock-rollback'}:
        current = store.workflow(owner, workflow.id)
        store.clock = lambda: current.deadline_at if mutation == 'deadline' else current.started_at - 1
    else:
        task = store.task(owner, root.id)
        store.command(owner, root.id, TaskCommand(operation=mutation, expected_revision=task.revision,
            idempotency_key='stop-before-completion'))
    with pytest.raises(StoreError):
        finish_author(prepared, review)
    assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 0
    assert store.budget(owner, workflow.id) == before
    assert store.task(owner, author.id).state != 'succeeded'


def test_two_connections_publish_one_receipt_and_event_with_lost_reply_replay(approved_task):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.completions import Completions
    from flycode.orchestration.store import Store

    prepared, review = approved_task
    store, owner, host, workflow, _, author, _, _, dispatcher, *_ = prepared
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    revision = store.task(owner, author.id).revision
    barrier = Barrier(2)
    def compete(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=5)
            return Completions(connection, dispatcher.root).complete(host, artifact.id, review.id,
                expected_revision=revision, key='same-lost-reply-key')
        finally:
            connection.close()
    before = store.budget(owner, workflow.id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(compete, range(2)))
    assert receipts[0] == receipts[1]
    assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 1
    assert sum(event.kind == 'task_completion_published' for event in store.events(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id) == before
    with pytest.raises(StoreError, match='idempotency'):
        service(prepared).complete(host, artifact.id, review.id, expected_revision=revision + 1, key='same-lost-reply-key')


def test_completion_outbox_failure_rolls_back_receipt_task_and_reservation(approved_task):
    import sqlite3

    prepared, review = approved_task
    store, owner, _, workflow, *_ = prepared
    tables = ('task_completions', 'tasks', 'attempts', 'reservations', 'instances', 'events', 'outbox', 'commands')
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables}
    store.db.execute("CREATE TRIGGER fail_completion BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'outbox unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match='outbox unavailable'):
        finish_author(prepared, review)
    assert {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables} == before
    store.db.execute('DROP TRIGGER fail_completion')
    proof = finish_author(prepared, review)
    assert service(prepared).current(owner, proof.id)
    assert store.workflow(owner, workflow.id).state == 'running'


@pytest.mark.parametrize('change', ['source', 'author-contract', 'cancel-author'])
def test_invalidation_revokes_active_descendant_without_freeing_its_reservation(approved_task, change):
    prepared, review = approved_task
    store, owner, host, workflow, root, author, _, _, _, source, _ = prepared
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Dependent writer',
        'parent_id': root.id, 'dependencies': (author.id,)}), key='running-child')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-running-child'))
    proof = finish_author(prepared, review)
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-running-child')).attempt
    assert attempt is not None
    before = store.budget(owner, workflow.id)
    if change == 'source':
        original = (source / 'dirty.bin').read_bytes()
        (source / 'dirty.bin').write_bytes(b'new owner data')
        store.refresh_results(owner, workflow.id)
        (source / 'dirty.bin').write_bytes(original)
    else:
        current = store.task(owner, author.id)
        store.command(owner, author.id, TaskCommand(operation='cancel' if change == 'cancel-author' else 'revise', expected_revision=current.revision,
            idempotency_key='changed-active-prerequisite', changes={} if change == 'cancel-author' else {'objective': 'A new requirement.'}))
    assert not service(prepared).current(owner, proof.id)
    assert store.task(owner, child.id).state == 'blocked'
    assert store.task(owner, child.id).fence > attempt.fence
    assert store._attempt(attempt.id).state == 'stopping'
    assert store.instance(host, instance.id).state == 'reconciling'
    assert store.budget(owner, workflow.id) == before
    with pytest.raises(StoreError, match='fence'):
        store.heartbeat(host, attempt.id, attempt.fence)
    store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    assert store.task(owner, child.id).current_attempt_id is None
    assert store.task(owner, author.id).state == ('cancelled' if change == 'cancel-author' else 'blocked')


@pytest.mark.parametrize('when', ['before-completion', 'after-completion', 'cancel-before-launch', 'late-own-evidence', 'reopened-review'])
def test_review_dependency_requires_its_own_current_evidence(approved_task, monkeypatch, when):
    from test_result_delivery import reviewer

    from flycode.orchestration.runtime import Runtime

    prepared, review = approved_task
    store, owner, host, workflow, root, author, first_reviewer, _, dispatcher, source, approval = prepared
    if when == 'reopened-review':
        from flycode.domain.orchestration import BudgetAmount

        old_review = review
        proof = finish_author(prepared, review)
        current = store.task(owner, first_reviewer.id)
        for operation, changes in [('reopen', {}), ('revise', {'budget': {'tool_calls': 8}}), ('enqueue', {})]:
            current = store.command(owner, current.id, TaskCommand(operation=operation, expected_revision=current.revision,
                idempotency_key='reopen-review-' + operation, changes=changes))
        current_root = store.task(owner, root.id)
        store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=current_root.revision,
            idempotency_key='restart-review-root'))
        store.start_next(owner, key='restart-review-workflow')
        instance = next(item for item in store.instances(host) if item.profile_id == current.allowed_profile_ids[0] and item.state == 'idle')
        selected = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=current.id, expected_task_revision=current.revision, reserve=BudgetAmount(tool_calls=4), idempotency_key='claim-new-review')).attempt
        assert selected is not None
        selected = store.confirm_started(host, selected.id, selected.fence)
        review = Reviews(store, dispatcher.root).run(host, selected.id, selected.fence, proof.artifact_id, approval_hash=approval)
        assert review.status == 'approved' and review.id != old_review.id
        assert store._attempt(old_review.reviewer_attempt_id).state == 'succeeded'
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Consume independently verified review',
        'parent_id': root.id, 'dependencies': (first_reviewer.id,)}), key='review-dependent-writer')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-review-dependent'))
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-review-dependent')).attempt
    assert attempt is not None
    if when == 'cancel-before-launch':
        current = store.task(owner, first_reviewer.id)
        before = store.budget(owner, workflow.id)
        store.command(owner, current.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-review-dependency'))
        assert store.task(owner, child.id).fence > attempt.fence
        assert store._attempt(attempt.id).state == 'stopping'
        assert store.budget(owner, workflow.id) == before
        with pytest.raises(StoreError, match='fence'):
            store.heartbeat(host, attempt.id, attempt.fence)
        store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
        return
    runtime = Runtime(store, dispatcher.root)
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
    finally:
        handle.close()
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, attempt.id)
    checker = reviewer((store, owner, host, workflow, child, attempt, source), [artifact], tools=4, parent_id=root.id)
    child_review = Reviews(store, dispatcher.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert child_review.status == 'approved'
    completion = service(prepared)
    if when == 'reopened-review':
        proof = completion.complete(host, artifact.id, child_review.id,
            expected_revision=store.task(owner, child.id).revision, key='finish-after-review-reopen')
        assert proof.prerequisites[first_reviewer.id].id == review.id
        assert completion.current(owner, proof.id)
        return
    if when == 'late-own-evidence':
        check = completion._review_evidence
        def lose_own_evidence(scope, identity):
            check(scope, identity)
            if identity == review.id:
                (dispatcher.root / 'verification' / child_review.verdict.evidence_id / 'manifest.json').unlink()
        monkeypatch.setattr(completion, '_review_evidence', lose_own_evidence)
        with pytest.raises(StoreError):
            completion.complete(host, artifact.id, child_review.id,
                expected_revision=store.task(owner, child.id).revision, key='late-evidence-completion')
        assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 0
        return
    if when == 'after-completion':
        proof = completion.complete(host, artifact.id, child_review.id,
            expected_revision=store.task(owner, child.id).revision, key='finish-review-dependent')
        assert proof.prerequisites[first_reviewer.id].id == review.id
        assert completion.current(owner, proof.id)
    (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
    if when == 'after-completion':
        assert not completion.current(owner, proof.id)
    else:
        with pytest.raises(StoreError):
            completion.complete(host, artifact.id, child_review.id,
                expected_revision=store.task(owner, child.id).revision, key='finish-review-dependent')
        assert store.task(owner, child.id).state == 'awaiting_review'


@pytest.mark.parametrize('mutation', ['evidence', 'artifact'])
def test_completion_rechecks_evidence_after_reading_owner_source(approved_task, monkeypatch, mutation):
    import flycode.orchestration.completions as module

    prepared, review = approved_task
    store, owner, _, _, _, author, _, _, dispatcher, source, _ = prepared
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    capture = module.capture
    altered = []
    def change_after_capture(path, *args, **kwargs):
        result = capture(path, *args, **kwargs)
        if path == source and not altered:
            altered.append(True)
            if mutation == 'evidence':
                (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
            else:
                (Artifacts(store, dispatcher.root).directory / artifact.id / 'target/src/parser.py').write_text('value = 9\n')
        return result
    monkeypatch.setattr(module, 'capture', change_after_capture)
    with pytest.raises(StoreError):
        finish_author(prepared, review)
    assert altered and store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 0


def test_schema22_interruption_preserves_prior_records(setup, monkeypatch):  # noqa: F811
    import sqlite3

    from flycode.orchestration.store import Store

    store, *_ = setup
    tables = ('profiles', 'instances', 'commands', 'events', 'outbox', 'reservations', 'tasks', 'workflows')
    before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables}
    store.db.execute('DROP TABLE task_completions')
    store.db.execute('PRAGMA user_version=21')
    connect = sqlite3.connect
    def interrupt(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '22' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', interrupt)
        with pytest.raises(sqlite3.DatabaseError):
            Store(store.path)
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 21
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='task_completions'").fetchone()
    assert {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables} == before
    connection = Store(store.path)
    try:
        assert connection.db.execute('PRAGMA user_version').fetchone()[0] == 22
        assert {table: [tuple(row) for row in connection.db.execute('SELECT * FROM ' + table)] for table in tables} == before
    finally:
        connection.close()


def test_cancelled_completion_stops_actual_descendant_worker_and_retains_late_http_spend(approved_task, model_server):
    import json
    from threading import Event

    from flycode.orchestration.runtime import Runtime

    prepared, review = approved_task
    store, owner, host, workflow, root, author, _, _, dispatcher, source, _ = prepared
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Actual dependent worker',
        'parent_id': root.id, 'dependencies': (author.id,)}), key='actual-dependent-worker')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-actual-dependent'))
    finish_author(prepared, review)
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-actual-dependent')).attempt
    assert attempt is not None
    entered, release = Event(), Event()
    def held(request):
        entered.set()
        assert release.wait(20)
        return model_server.completion(json.dumps({'candidates': [{
            'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    model_server.respond = held
    runtime = Runtime(store, dispatcher.root)
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(15), handle.error
        before = store.budget(owner, workflow.id)
        current = store.task(owner, author.id)
        store.command(owner, author.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-active-predecessor'))
        assert store.task(owner, child.id).fence > attempt.fence
        assert store.budget(owner, workflow.id) == before
        release.set()
        assert handle.finished.wait(25), handle.error
        assert handle.process_stopped.is_set() and runtime.worker(owner, attempt.id).stop_confirmed
        assert not store.db.execute('SELECT 1 FROM artifacts WHERE attempt_id=?', (attempt.id,)).fetchone()
        calls = [json.loads(row['data']) for row in store.db.execute('SELECT data FROM model_calls WHERE attempt_id=?', (attempt.id,))]
        assert len(calls) == 1 and calls[0]['late'] and calls[0]['status'] == 'completed'
        assert store.budget(owner, workflow.id).spent == before.spent
        assert store.task(owner, child.id).state != 'succeeded'
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    finally:
        release.set()
        handle.close()


@pytest.mark.parametrize('role,project', [('reader', 'p'), ('owner', 'q'), ('runtime', 'q')])
def test_completion_denies_untrusted_scope_before_subject_inspection(approved_task, monkeypatch, role, project):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.sources import Sources

    prepared, review = approved_task
    store, owner, _, workflow, _, author, _, _, dispatcher, *_ = prepared
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    scope = AccessScope(actor_id='foreign-completion', project_ids={project}, role=role)
    monkeypatch.setattr(Sources, 'prepare', lambda *a, **k: pytest.fail('Untrusted completion inspected subject bytes'))
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='scope'):
        service(prepared).complete(scope, artifact.id, review.id, expected_revision=store.task(owner, author.id).revision, key='denied-completion')
    assert store.task(owner, author.id).state == 'awaiting_review'
    assert store.db.execute('SELECT count(*) FROM task_completions').fetchone()[0] == 0
    assert store.budget(owner, workflow.id) == before
