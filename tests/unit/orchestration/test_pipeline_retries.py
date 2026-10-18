"""Rejected immutable output can be corrected only under bounded owner authority."""

import json

import pytest
from test_parent_continuation import (  # noqa: F401
    approve_parent,
    parent_ready,
    review_ready,
    wait_handles,
)
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import BudgetAmount, TaskCommand
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.review import Reviews


@pytest.fixture
def retry_ready(parent_ready, model_server):  # noqa: F811
    p = parent_ready
    store, owner, dispatcher = p['store'], p['owner'], p['dispatcher']
    integrator = store.command(owner, p['integrator'].id, TaskCommand(operation='revise', expected_revision=p['integrator'].revision,
        idempotency_key='draft-retry-integrator', changes={'dependencies': ()}))
    child = store.command(owner, p['child'].id, TaskCommand(operation='revise', expected_revision=p['child'].revision,
        idempotency_key='retry-child-lifetime', changes={'budget': BudgetAmount(model_calls=2, tool_calls=2).model_dump()}))
    reviewer = store.command(owner, p['child_review'].id, TaskCommand(operation='revise', expected_revision=p['child_review'].revision,
        idempotency_key='retry-review-lifetime', changes={'budget': BudgetAmount(tool_calls=8).model_dump()}))
    previous = dispatcher.binding(owner, child.id)
    dispatcher.bind(owner, child.id, p['source'], expected_revision=child.revision,
        expected_binding_id=previous.id, key='bind-retry-child', brain='heuristic_fsm')
    integrator = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='restore-retry-integrator', changes={'dependencies': (reviewer.id, p['parent_review'].id)}))
    p.update(child=child, child_review=reviewer, integrator=integrator)
    original = model_server.respond
    calls = []
    def respond(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return original(request)
        assert child.objective in messages
        calls.append(request)
        assert len(calls) <= 2, 'Owner policy permitted only two child attempts'
        if len(calls) == 2:
            assert 'changes_requested' in messages and 'value = -1' in messages, 'Retry lost exact failed bytes or review feedback'
            assert 'tests/test_parser.py::test_parse' in messages and 'assert -1 == 1' in messages, 'Retry lost failed-case diagnostics'
        arguments = {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'} if len(calls) == 1 else {
            'path': 'src/parser.py', 'old_text': 'value = -1', 'new_text': 'value = 1'}
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
    model_server.respond = respond
    p['retry_policies'] = {child.id: {'max_retries': 1, 'writer_budget': {'model_calls': 1, 'tool_calls': 1},
        'reviewer_budget': {'model_calls': 0, 'tool_calls': 4}}}
    return p


def test_rejected_child_retries_exact_failed_bytes_and_feedback_before_fresh_verified_completion(retry_ready, monkeypatch):
    p = retry_ready
    store, owner, host, dispatcher, source = (p[key] for key in ('store', 'owner', 'host', 'dispatcher', 'source'))
    before = store.budget(owner, p['workflow'].id)
    revisions = {task.id: task.revision for task in store.tasks(owner, p['workflow'].id)}
    recipe = approve_parent(p, retry_policies=p['retry_policies'], expected_revisions=revisions)
    assert approve_parent(p, retry_policies=p['retry_policies'], expected_revisions=revisions) == recipe
    assert store.attempts(owner, p['workflow'].id) == [] and store.budget(owner, p['workflow'].id) == before
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Retry used a manual claim'))
    monkeypatch.setattr(store, 'command', lambda *a, **kw: pytest.fail('Retry required another owner command'))
    try:
        deadline = None
        for number in range(25):
            dispatcher.tick(host, key=f'retry-route-{number}')
            wait_handles(dispatcher)
            workflow = store.workflow(owner, p['workflow'].id)
            if deadline is None:
                deadline = workflow.deadline_at
            assert workflow.deadline_at == deadline
            if workflow.state == 'succeeded':
                break
            assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert store.workflow(owner, p['workflow'].id).state == 'succeeded'
        attempts = store.attempts(owner, p['workflow'].id)
        assert len(attempts) == 8
        authors = [attempt for attempt in attempts if attempt.task_id == p['child'].id]
        assert len(authors) == 2 and authors[1].fence > authors[0].fence
        assert [attempt.state for attempt in authors] == ['interrupted', 'succeeded']
        assert all(attempt.initial_budget == BudgetAmount(model_calls=1, tool_calls=1) for attempt in authors)
        assert sum(attempt.spent.model_calls for attempt in authors) == 2
        assert sum(attempt.spent.tool_calls for attempt in authors) == 2
        patches = [Artifacts(store, dispatcher.root).for_attempt(owner, attempt.id) for attempt in authors]
        assert patches[0].id != patches[1].id
        assert (Artifacts(store, dispatcher.root).directory / patches[0].id / 'target/src/parser.py').read_text() == 'value = -1\n'
        rows = store.db.execute('SELECT id FROM reviews WHERE artifact_id IN (?,?)', (patches[0].id, patches[1].id)).fetchall()
        reviews = [Reviews(store, dispatcher.root).get(owner, row['id']) for row in rows]
        assert len(reviews) == 2 and {review.status for review in reviews} == {'changes_requested', 'approved'}
        assert store.task(owner, p['child'].id).completion_id is not None
        result_id = store.task(owner, p['parent'].id).result_id
        assert Integration(store, dispatcher.root).current(owner, result_id)
        assert (source / 'src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'src/note.py').read_text() == 'phase = 1\n'
        assert (source / 'src/extra.py').read_text() == 'value = 4\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert len([event for event in store.events(owner, p['workflow'].id) if event.kind == 'pipeline_retry_ready']) == 1
        failed_review = next(review for review in reviews if review.status == 'changes_requested')
        retained = [dispatcher.root / 'verification' / failed_review.verdict.evidence_id / 'manifest.json',
            dispatcher.root / 'artifacts' / patches[0].id / 'target/src/parser.py']
        for path in retained:
            raw = path.read_bytes()
            path.unlink()
            assert not Integration(store, dispatcher.root).current(owner, result_id)
            path.write_bytes(raw)
            assert Integration(store, dispatcher.root).current(owner, result_id)
    finally:
        for handle in dispatcher.handles.values():
            handle.close()



def test_resumed_parent_receives_pinned_previous_observations_in_actual_http(parent_ready, model_server, monkeypatch):  # noqa: F811
    from test_parent_continuation import (
        test_parent_releases_slot_restores_partial_bytes_and_resumes_after_exact_child_completion,
    )

    p = parent_ready
    original = model_server.respond
    calls = []
    def inspect(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            calls.append(request)
            if len(calls) == 2:
                assert 'Immutable previous attempt data' in messages and 'src/note.py' in messages, 'Parent checkpoint was dropped before actual HTTP'
        return original(request)
    model_server.respond = inspect
    test_parent_releases_slot_restores_partial_bytes_and_resumes_after_exact_child_completion(p, monkeypatch)



def test_repeated_rejected_output_exhausts_policy_without_third_author_or_refund(retry_ready, model_server):
    p = retry_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    original = model_server.respond
    calls = []
    def reject(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return original(request)
        calls.append(request)
        assert len(calls) <= 2
        arguments = {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'} if len(calls) == 1 else {
            'path': 'src/parser.py', 'old_text': 'value = -1', 'new_text': 'value = -2'}
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
    model_server.respond = reject
    approve_parent(p, retry_policies=p['retry_policies'])
    try:
        for number in range(25):
            dispatcher.tick(host, key=f'exhaust-retry-{number}')
            wait_handles(dispatcher)
        child = store.task(owner, p['child'].id)
        assert child.state == 'failed' and child.blocked_reason == 'retry_exhausted' and child.current_attempt_id is None
        attempts = store.attempts(owner, p['workflow'].id)
        assert len(attempts) == 5 and len([item for item in attempts if item.task_id == child.id]) == 2
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent == BudgetAmount(model_calls=3, tool_calls=7)
        assert ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert len([event for event in store.events(owner, p['workflow'].id) if event.kind == 'pipeline_retry_blocked']) == 1
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.fixture
def rejected_retry_boundary(retry_ready):
    p = retry_ready
    recipe = approve_parent(p, retry_policies=p['retry_policies'])
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    try:
        for number in range(8):
            dispatcher.tick(host, key=f'before-retry-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'failed':
                break
        assert store.task(owner, p['child_review'].id).state == 'failed'
        assert store.task(owner, p['child'].id).state == 'awaiting_review'
        yield p, recipe
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_runtime_cannot_replace_the_owner_retry_policy(rejected_retry_boundary):
    from flycode.orchestration.retries import Retries
    from flycode.orchestration.store import StoreError

    p, recipe = rejected_retry_boundary
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    policy = recipe.retry_policies[p['child'].id].model_copy(update={'max_retries': 2})
    forged = recipe.model_copy(update={'retry_policies': {p['child'].id: policy}})
    service = Pipelines(store, dispatcher.root)
    _, tasks, receipts = service._guard(host, recipe)
    before = store.tasks(host, p['workflow'].id), store.budget(host, p['workflow'].id), store.outbox_count()
    with pytest.raises(StoreError, match='pipeline_changed'):
        Retries(store, dispatcher.root).advance(host, service, forged, tasks, receipts)
    assert (store.tasks(host, p['workflow'].id), store.budget(host, p['workflow'].id), store.outbox_count()) == before


@pytest.mark.parametrize('same_file', [False, True])
def test_parent_retry_fits_three_attempts_and_retains_preparation_and_child_proofs(parent_ready, model_server, same_file):  # noqa: F811
    p = parent_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    integrator = store.command(owner, p['integrator'].id, TaskCommand(operation='revise', expected_revision=p['integrator'].revision,
        idempotency_key='draft-parent-retry-integrator', changes={'dependencies': ()}))
    parent = store.command(owner, p['parent'].id, TaskCommand(operation='revise', expected_revision=p['parent'].revision,
        idempotency_key='parent-retry-lifetime', changes={'budget': BudgetAmount(model_calls=3, tool_calls=3).model_dump()}))
    reviewer = store.command(owner, p['parent_review'].id, TaskCommand(operation='revise', expected_revision=p['parent_review'].revision,
        idempotency_key='parent-retry-review-lifetime', changes={'budget': BudgetAmount(tool_calls=8).model_dump()}))
    integrator = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='parent-retry-integrator', changes={'dependencies': (p['child_review'].id, reviewer.id)}))
    previous = dispatcher.binding(owner, parent.id)
    dispatcher.bind(owner, parent.id, p['source'], expected_revision=parent.revision, expected_binding_id=previous.id,
        key='bind-parent-retry', brain='heuristic_fsm')
    p.update(parent=parent, parent_review=reviewer, integrator=integrator)
    if same_file:
        from flycode.verification.engine import VerificationEngine

        oracle = p['source'].parent / 'retry-parent-oracle/tests'
        oracle.mkdir(parents=True)
        (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n'
            '    assert runpy.run_path("src/parser.py")["value"] == 1\n'
            '    assert runpy.run_path("src/note.py")["phase"] == 3\n')
        engine = VerificationEngine(dispatcher.root / 'verification')
        checks = engine.oracles.read(p['approvals'][parent.id]).checks
        approval = engine.oracles.approve(parent.acceptance, checks, oracle.parent)
        p['approvals'].update({identity: approval for identity in (parent.id, reviewer.id, integrator.id)})
    original = model_server.respond
    calls = []
    def correct_parent(request):
        messages = str(request['messages'])
        if parent.objective not in messages:
            return original(request)
        calls.append(request)
        if len(calls) == 1 and not same_file:
            return original(request)
        assert len(calls) <= 3
        if len(calls) == 3:
            assert 'changes_requested' in messages and 'Immutable previous attempt data' in messages
        arguments = {'path': 'src/extra.py', 'old_text': 'value = 0', 'new_text': 'value = -1'} if len(calls) == 2 else {
            'path': 'src/extra.py', 'old_text': 'value = -1', 'new_text': 'value = 4'}
        if same_file:
            arguments = {'path': 'src/note.py', 'old_text': f'phase = {len(calls) - 1}', 'new_text': f'phase = {len(calls)}'}
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
    model_server.respond = correct_parent
    policies = {parent.id: {'max_retries': 1, 'writer_budget': {'model_calls': 1, 'tool_calls': 1},
        'reviewer_budget': {'tool_calls': 4}}}
    approve_parent(p, retry_policies=policies)
    try:
        deadline = None
        for number in range(25):
            dispatcher.tick(host, key=f'parent-retry-{number}')
            wait_handles(dispatcher)
            workflow = store.workflow(owner, p['workflow'].id)
            if deadline is None:
                deadline = workflow.deadline_at
            assert workflow.deadline_at == deadline
            if workflow.state == 'succeeded':
                break
        assert store.workflow(owner, p['workflow'].id).state == 'succeeded'
        attempts = [item for item in store.attempts(owner, p['workflow'].id) if item.task_id == parent.id]
        assert len(attempts) == 3 and [item.state for item in attempts] == ['interrupted', 'interrupted', 'succeeded']
        assert [item.fence for item in attempts] == sorted({item.fence for item in attempts})
        assert all(item.initial_budget == BudgetAmount(model_calls=1, tool_calls=1) for item in attempts)
        child_proof = store.task(owner, p['child'].id).completion_id
        assert attempts[-1].prerequisites[p['child'].id].id == child_proof
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        result_id = store.task(owner, parent.id).result_id
        assert Integration(store, dispatcher.root).current(owner, result_id)
        assert (p['source'] / ('src/note.py' if same_file else 'src/extra.py')).read_text() == ('phase = 3\n' if same_file else 'value = 4\n')
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('table,field,value', [
    ('review_bindings', 'revision', 2),
    ('review_executions', 'attempt_id', 'other-attempt'),
    ('review_executions', 'task_id', 'other-task'),
    ('review_executions', 'project_id', 'q'),
    ('review_executions', 'workflow_id', 'other-workflow'),
    ('review_executions', 'reruns', 1),
    ('attempts', 'task_id', 'other-task'),
    ('attempts', 'workflow_id', 'other-workflow'),
])
def test_retry_rejects_changed_negative_review_execution_ownership(rejected_retry_boundary, table, field, value):
    from flycode.orchestration.retries import Retries
    from flycode.orchestration.store import StoreError

    p, recipe = rejected_retry_boundary
    store, host, root = p['store'], p['host'], p['dispatcher'].root
    service = Pipelines(store, root)
    _, tasks, receipts = service._guard(host, recipe)
    author = tasks[p['child'].id].current_attempt_id
    reviewer = store.attempts(host, recipe.workflow_id)[-1].id
    identity = author if table == 'attempts' else reviewer
    if table == 'review_bindings':
        identity = json.loads(receipts['review:' + p['child_review'].id]['binding'])['id']
    row = store.db.execute(f'SELECT data FROM {table} WHERE id=?', (identity,)).fetchone()
    raw = json.loads(row['data'])
    raw[field] = value
    store.db.execute(f'UPDATE {table} SET data=? WHERE id=?', (json.dumps(raw), identity))
    before = store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()
    with pytest.raises(StoreError, match='retry_changed'):
        Retries(store, root).advance(host, service, recipe, tasks, receipts)
    assert (store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()) == before


@pytest.fixture
def rejected_second_boundary(retry_ready, model_server):
    p = retry_ready
    original = model_server.respond
    calls = []
    def reject(request):
        if p['parent'].objective in str(request['messages']):
            return original(request)
        calls.append(request)
        assert len(calls) <= 2
        arguments = {'path': 'src/parser.py', 'old_text': f'value = {0 if len(calls) == 1 else -1}',
            'new_text': f'value = {-len(calls)}'}
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
    model_server.respond = reject
    recipe = approve_parent(p, retry_policies=p['retry_policies'])
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'second-review-boundary-{number}')
            wait_handles(dispatcher)
            reviews = store.db.execute('SELECT id FROM reviews').fetchall()
            if len(reviews) == 2:
                break
        assert len(reviews) == 2 and store.task(host, p['child'].id).state == 'awaiting_review'
        yield p, recipe
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('mutation', ['manifest', 'deadline', 'rollback'])
def test_retry_exhaustion_requires_current_negative_evidence_and_time(rejected_second_boundary, mutation):
    from flycode.orchestration.retries import Retries
    from flycode.orchestration.store import StoreError

    p, recipe = rejected_second_boundary
    store, host, root = p['store'], p['host'], p['dispatcher'].root
    service = Pipelines(store, root)
    _, tasks, receipts = service._guard(host, recipe)
    workflow = store.workflow(host, recipe.workflow_id)
    review_id = store.db.execute('SELECT id FROM reviews ORDER BY rowid DESC LIMIT 1').fetchone()['id']
    review = Reviews(store, root).get(host, review_id)
    if mutation == 'manifest':
        (root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
    else:
        store.clock = lambda: workflow.deadline_at if mutation == 'deadline' else workflow.started_at - 1
    before = store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()
    with pytest.raises(StoreError, match='retry_evidence' if mutation == 'manifest' else 'wall_deadline'):
        Retries(store, root).advance(host, service, recipe, tasks, receipts)
    assert (store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()) == before


def test_retry_saves_budget_blocker_without_new_assignment_or_refund(retry_ready):
    from flycode.orchestration.store import changed

    p = retry_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    workflow = store.workflow(owner, p['workflow'].id)
    # This fixture starts with a smaller owner budget; all work below uses real debits.
    store._save_workflow(changed(workflow, budget=workflow.budget.model_copy(update={'max_model_calls': 3})))
    approve_parent(p, retry_policies=p['retry_policies'])
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'retry-budget-{number}')
            wait_handles(dispatcher)
        child = store.task(owner, p['child'].id)
        assert child.state == 'failed' and child.blocked_reason == 'retry_budget' and child.current_attempt_id is None
        assert len(store.attempts(owner, workflow.id)) == 3
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent == BudgetAmount(model_calls=2, tool_calls=4)
        assert ledger.reserved == BudgetAmount() and not ledger.uncertain
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_retry_transition_is_atomic_across_connections(rejected_retry_boundary, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration import retries
    from flycode.orchestration.store import Store

    p, recipe = rejected_retry_boundary
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    barrier = Barrier(2)
    original = retries.capture
    def synchronize(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == p['source']:
            barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(retries, 'capture', synchronize)
    before = store.budget(host, recipe.workflow_id)
    def advance(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Pipelines(connection, dispatcher.root).advance(host, recipe.workflow_id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(advance, range(2))) == [(), ()]
    assert store.task(host, p['child'].id).state == 'ready'
    assert store.db.execute("SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'retry:%'", (recipe.id,)).fetchone()[0] == 1
    assert len([event for event in store.events(host, recipe.workflow_id) if event.kind == 'pipeline_retry_ready']) == 1
    assert store.budget(host, recipe.workflow_id) == before


def test_retry_rolls_back_outbox_failure_and_replays_after_restart(rejected_retry_boundary, monkeypatch):
    from flycode.orchestration.store import Store

    p, recipe = rejected_retry_boundary
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    before = store.tasks(host, recipe.workflow_id), store.attempts(host, recipe.workflow_id), store.budget(host, recipe.workflow_id)
    original = store._event
    def fail(scope, workflow, kind, **kwargs):
        if kind == 'pipeline_retry_ready':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        Pipelines(store, dispatcher.root).advance(host, recipe.workflow_id)
    assert (store.tasks(host, recipe.workflow_id), store.attempts(host, recipe.workflow_id), store.budget(host, recipe.workflow_id)) == before
    assert not store.db.execute("SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'retry:%'", (recipe.id,)).fetchone()
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        service = Pipelines(connection, dispatcher.root)
        assert service.advance(host, recipe.workflow_id) == ()
        saved = connection.task(host, p['child'].id)
        assert service.advance(host, recipe.workflow_id) == ()
        assert connection.task(host, p['child'].id) == saved
    finally:
        connection.close()
    assert len([event for event in store.events(host, recipe.workflow_id) if event.kind == 'pipeline_retry_ready']) == 1
    assert store.budget(host, recipe.workflow_id) == before[2]


@pytest.mark.parametrize('automatic', [False, True])
def test_owner_retry_caps_apply_to_all_canonical_claims(rejected_retry_boundary, automatic):
    from flycode.domain.orchestration import AutoClaimRequest, ClaimRequest

    p, recipe = rejected_retry_boundary
    store, host = p['store'], p['host']
    assert Pipelines(store, p['dispatcher'].root).advance(host, recipe.workflow_id) == ()
    task = store.task(host, p['child'].id)
    instance = next(item for item in store.instances(host) if item.profile_id in task.allowed_profile_ids)
    before = store.budget(host, recipe.workflow_id)
    denied = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=BudgetAmount(model_calls=2, tool_calls=2),
        idempotency_key='manual-cannot-spend-lifetime'))
    assert denied.attempt is None and 'attempt_budget' in denied.reasons
    assert store.budget(host, recipe.workflow_id) == before
    request = {'instance_id': instance.id, 'expected_instance_revision': instance.revision, 'idempotency_key': 'capped-retry-claim'}
    if automatic:
        result = store.claim_next(host, AutoClaimRequest(**request))
    else:
        result = store.claim(host, ClaimRequest(**request, task_id=task.id, expected_task_revision=task.revision,
            reserve=BudgetAmount(model_calls=1, tool_calls=1)))
    assert result.attempt is not None and result.attempt.task_id == task.id
    assert result.attempt.initial_budget == BudgetAmount(model_calls=1, tool_calls=1)
    assert store.budget(host, recipe.workflow_id).spent == before.spent


@pytest.mark.parametrize('mutation', ['foreign', 'bool', 'bool_model', 'zero', 'three', 'writer_zero', 'reviewer_models', 'reviewer_zero', 'underfunded', 'parent_two'])
def test_invalid_retry_authority_is_inert_before_filesystem_preflight(retry_ready, mutation, monkeypatch):
    from pydantic import ValidationError

    from flycode.orchestration import pipeline
    from flycode.orchestration.store import StoreError

    p = retry_ready
    policy = json.loads(json.dumps(p['retry_policies']))
    child = policy[p['child'].id]
    if mutation == 'foreign':
        policy['unapproved-writer'] = policy.pop(p['child'].id)
    elif mutation == 'bool_model':
        from flycode.domain.pipeline import RetryPolicy

        policy[p['child'].id] = RetryPolicy.model_validate(child).model_copy(update={'max_retries': True})
    elif mutation in {'bool', 'zero', 'three'}:
        child['max_retries'] = {'bool': True, 'zero': 0, 'three': 3}[mutation]
    elif mutation == 'writer_zero':
        child['writer_budget']['model_calls'] = 0
    elif mutation == 'reviewer_models':
        child['reviewer_budget']['model_calls'] = 1
    elif mutation == 'reviewer_zero':
        child['reviewer_budget']['tool_calls'] = 0
    elif mutation == 'underfunded':
        child['writer_budget']['model_calls'] = 2
    else:
        child['max_retries'] = 2
        policy = {p['parent'].id: child}
    store, owner = p['store'], p['owner']
    before = store.tasks(owner, p['workflow'].id), store.budget(owner, p['workflow'].id), store.outbox_count()
    monkeypatch.setattr(pipeline, 'capture', lambda *a, **kw: pytest.fail('Invalid owner authority reached source preflight'))
    with pytest.raises((StoreError, ValidationError)):
        approve_parent(p, retry_policies=policy)
    assert (store.tasks(owner, p['workflow'].id), store.budget(owner, p['workflow'].id), store.outbox_count()) == before
    assert store.attempts(owner, p['workflow'].id) == []


@pytest.mark.parametrize('retained', ['pre_continuation', 'pre_retry'])
def test_retained_review_hash_without_new_retry_field_executes_real_review(review_ready, owned, retained):  # noqa: F811
    import hashlib

    from flycode.domain.results import PatchArtifact
    from flycode.orchestration.store import changed
    from flycode.verification.snapshot import digest

    dispatcher, artifact, approval, task, *_ = review_ready
    store, owner, host, workflow, *_ = owned
    absent = {'retry', 'continuation', 'prerequisites'} if retained == 'pre_continuation' else {'retry'}
    raw = artifact.model_dump_json(exclude={'patch_hash', *absent})
    legacy = artifact.model_dump(mode='json', exclude=absent)
    legacy['patch_hash'] = hashlib.sha256(raw.encode()).hexdigest()
    (Artifacts(store, dispatcher.root).directory / artifact.id / 'manifest.json').write_text(raw)
    store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(legacy), artifact.id))
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='pre-retry-patch', changes={'input_artifacts': {artifact.id: legacy['patch_hash']}}))
    binding = dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='retained-review')
    hash_absent = {'continuation', 'retry'} if retained == 'pre_continuation' else {'retry'}
    binding = changed(binding, artifact_hash=digest(PatchArtifact.model_validate(legacy).model_dump(mode='json', exclude=hash_absent)))
    store.db.execute('UPDATE review_bindings SET data=? WHERE id=?', (binding.model_dump_json(), binding.id))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key='enqueue-retained-review'))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='run-retained-review')
    assert result.attempt is not None, result.reasons
    assert store.task(owner, task.id).state == 'succeeded'
    assert store.budget(owner, workflow.id).spent.tool_calls == before.spent.tool_calls + 4


@pytest.mark.parametrize('change', ['pause', 'cancel', 'criteria', 'inconclusive', 'uncertain'])
def test_retry_cannot_override_changed_owner_authority_or_unknown_review(rejected_retry_boundary, change):
    from flycode.domain.results import ReviewRecord
    from flycode.orchestration.store import changed

    p, recipe = rejected_retry_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    task = store.task(owner, p['child'].id)
    if change in {'pause', 'cancel'}:
        store.command(owner, task.id, TaskCommand(operation=change, expected_revision=task.revision, idempotency_key='retry-owner-' + change))
    elif change == 'criteria':
        task = store.task(owner, p['child_review'].id)
        contract = task.acceptance.model_copy(update={'version': task.acceptance.version + 1})
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='retry-new-criteria', changes={'acceptance': contract.model_dump(mode='json')}))
    else:
        review_id = store.db.execute('SELECT id FROM reviews').fetchone()['id']
        review = store._load('reviews', review_id, ReviewRecord)
        if change == 'inconclusive':
            review = changed(review, status='inconclusive', verdict=changed(review.verdict, status='inconclusive'))
            store.db.execute('UPDATE reviews SET data=? WHERE id=?', (review.model_dump_json(), review.id))
        else:
            store.db.execute('UPDATE reservations SET uncertain=1 WHERE attempt_id=?', (review.reviewer_attempt_id,))
    before = store.budget(owner, recipe.workflow_id)
    Pipelines(store, dispatcher.root).advance(host, recipe.workflow_id)
    assert not store.db.execute("SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'retry:%'", (recipe.id,)).fetchone()
    assert store.budget(owner, recipe.workflow_id).spent == before.spent
    assert len(store.attempts(owner, recipe.workflow_id)) == 3


@pytest.mark.parametrize('boundary', ['retry', 'exhausted'])
def test_retry_rechecks_negative_execution_after_preflight(request, boundary, monkeypatch):
    from flycode.domain.results import ReviewExecution
    from flycode.orchestration.retries import Retries
    from flycode.orchestration.store import StoreError, changed

    p, recipe = request.getfixturevalue('rejected_retry_boundary' if boundary == 'retry' else 'rejected_second_boundary')
    store, host, root = p['store'], p['host'], p['dispatcher'].root
    original = Retries.evidence
    def mutate(service, scope, saved, **kwargs):
        result = original(service, scope, saved, **kwargs)
        if not kwargs.get('retired', True):
            execution = store._load('review_executions', saved.reviewer_attempt_id, ReviewExecution)
            execution = changed(execution, reruns=1)
            store.db.execute('UPDATE review_executions SET data=? WHERE id=?', (execution.model_dump_json(), execution.attempt_id))
        return result
    monkeypatch.setattr(Retries, 'evidence', mutate)
    service = Pipelines(store, root)
    _, tasks, receipts = service._guard(host, recipe)
    before = store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()
    with pytest.raises(StoreError, match='retry_changed'):
        Retries(store, root).advance(host, service, recipe, tasks, receipts)
    assert (store.tasks(host, recipe.workflow_id), store.budget(host, recipe.workflow_id), store.outbox_count()) == before


def test_two_owner_approved_retries_stop_at_global_three_attempts_and_keep_all_failures(retry_ready, model_server):
    p = retry_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    integrator = store.command(owner, p['integrator'].id, TaskCommand(operation='revise', expected_revision=p['integrator'].revision,
        idempotency_key='three-attempt-integrator-draft', changes={'dependencies': ()}))
    for name, budget in [('child', BudgetAmount(model_calls=3, tool_calls=3)), ('child_review', BudgetAmount(tool_calls=12))]:
        task = p[name]
        p[name] = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='three-attempt-' + name, changes={'budget': budget.model_dump()}))
    p['integrator'] = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='three-attempt-integrator-restore', changes={'dependencies': (p['child_review'].id, p['parent_review'].id)}))
    previous = dispatcher.binding(owner, p['child'].id)
    dispatcher.bind(owner, p['child'].id, p['source'], expected_revision=p['child'].revision,
        expected_binding_id=previous.id, key='bind-three-attempt-child', brain='heuristic_fsm')
    policies = p['retry_policies']
    policies[p['child'].id]['max_retries'] = 2
    original = model_server.respond
    calls = []
    def third_passes(request):
        if p['parent'].objective in str(request['messages']):
            return original(request)
        calls.append(request)
        assert len(calls) <= 3
        before, after = [(0, -1), (-1, -2), (-2, 1)][len(calls) - 1]
        if len(calls) > 1:
            assert 'changes_requested' in str(request['messages'])
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': f'value = {before}', 'new_text': f'value = {after}'}}]}))
    model_server.respond = third_passes
    approve_parent(p, retry_policies=policies)
    try:
        for number in range(30):
            dispatcher.tick(host, key=f'three-attempts-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, p['workflow'].id).state == 'succeeded':
                break
        assert store.workflow(owner, p['workflow'].id).state == 'succeeded'
        authors = [item for item in store.attempts(owner, p['workflow'].id) if item.task_id == p['child'].id]
        assert [item.state for item in authors] == ['interrupted', 'interrupted', 'succeeded']
        artifacts = [Artifacts(store, dispatcher.root).for_attempt(owner, item.id) for item in authors]
        assert [item.retry.number for item in artifacts[1:]] == [1, 2]
        assert artifacts[2].retry.artifact_id == artifacts[1].id and artifacts[1].retry.artifact_id == artifacts[0].id
        assert len(store.db.execute('SELECT id FROM reviews').fetchall()) == 4
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent.model_calls == 5 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_retry_evidence_changed_during_http_discards_paid_candidate(rejected_retry_boundary, model_server):
    p, recipe = rejected_retry_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    review = Reviews(store, dispatcher.root).get(owner, store.db.execute('SELECT id FROM reviews').fetchone()['id'])
    path = dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json'
    original = model_server.respond
    def revoke_after_response(request):
        response = original(request)
        path.unlink()
        return response
    model_server.respond = revoke_after_response
    before = store.budget(owner, recipe.workflow_id)
    result = dispatcher.tick(host, key='tamper-retry-http')
    assert result.attempt is not None and result.attempt.task_id == p['child'].id
    handle = dispatcher.handles[result.attempt.id]
    assert handle.finished.wait(25) and handle.process_stopped.is_set()
    after = store.budget(owner, recipe.workflow_id)
    assert after.spent.model_calls == before.spent.model_calls + 1
    assert after.spent.tool_calls == before.spent.tool_calls and not after.uncertain
    assert not store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (result.attempt.id,)).fetchone()
    repository = dispatcher.runtime.workspaces.repository(owner, result.attempt.id)
    assert (repository / 'src/parser.py').read_text() == 'value = -1\n'
    assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'


@pytest.mark.parametrize('operation', ['pause', 'cancel'])
def test_rejected_publication_preserves_owner_stop_semantics(owned, tmp_path, monkeypatch, operation):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, task, attempt, source = owned
    runtime = Runtime(store, tmp_path / 'runtime')
    original = Artifacts.publish
    def stop_before_publish(service, scope, attempt_id, fence):
        current = service.store.task(owner, task.id)
        service.store.command(owner, current.id, TaskCommand(operation=operation, expected_revision=current.revision,
            idempotency_key='stop-before-artifact-publication'))
        return original(service, scope, attempt_id, fence)
    monkeypatch.setattr(Artifacts, 'publish', stop_before_publish)
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(20) and handle.process_stopped.is_set()
        current = store.task(owner, task.id)
        assert current.state == ('retry_wait' if operation == 'pause' else 'cancelled')
        assert store._attempt(attempt.id).state == ('interrupted' if operation == 'pause' else 'cancelled')
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls > 0 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        if operation == 'pause':
            resumed = store.command(owner, current.id, TaskCommand(operation='resume', expected_revision=current.revision,
                idempotency_key='resume-interrupted-publication'))
            assert resumed.state == 'retry_wait' and not resumed.paused
            queued = store.command(owner, resumed.id, TaskCommand(operation='retry', expected_revision=resumed.revision,
                idempotency_key='retry-after-publication-pause'))
            assert queued.state == 'ready'
    finally:
        handle.close()


def test_rejected_publication_keeps_reservation_when_model_outcome_is_unsettled(owned, tmp_path, monkeypatch):
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.store import StoreError, changed

    store, owner, host, workflow, task, attempt, source = owned
    runtime = Runtime(store, tmp_path / 'runtime')
    captured = {}
    def reject_with_unknown_call(service, scope, attempt_id, fence):
        connection = service.store
        call = Resources(connection).calls(scope, workflow.id)[-1]
        captured['paid'] = connection.budget(scope, workflow.id).spent
        call = changed(call, status='uncertain')
        connection.db.execute('UPDATE model_calls SET state=?,data=? WHERE id=?', ('uncertain', call.model_dump_json(), call.id))
        raise StoreError('artifact_integrity')
    monkeypatch.setattr(Artifacts, 'publish', reject_with_unknown_call)
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(20) and handle.process_stopped.is_set()
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent == captured['paid'] and ledger.uncertain and ledger.reserved.tool_calls > 0
        assert store._attempt(attempt.id).state == 'reconciling'
        assert store.task(owner, task.id).current_attempt_id == attempt.id
        with pytest.raises(StoreError, match='resource_busy'):
            store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    finally:
        handle.close()
