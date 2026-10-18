"""Owner graph revisions preserve attempts and immutable previous generations."""

import json

import pytest
from test_parent_continuation import (  # noqa: F401
    approve_parent,
    parent_ready,
    review_ready,
    wait_handles,
)
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_pipeline_retries import rejected_retry_boundary, retry_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import BudgetAmount
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines


def revise_child(p, recipe, **changes):
    store, owner = p['store'], p['owner']
    service = Pipelines(store, p['dispatcher'].root)
    assert hasattr(service, 'replan'), 'Owner-approved immutable DAG revisions are missing'
    arguments = {'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)},
        'key': 'replan-child', 'changes': {p['child'].id: {'objective': 'Repair child parser from the original source; set value to 1.'}}}
    arguments.update(changes)
    return service.replan(owner, recipe.id, **arguments), arguments


def test_owner_replans_rejected_child_then_fresh_review_parent_and_integration(rejected_retry_boundary, model_server):  # noqa: F811
    p, recipe = rejected_retry_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    base = store.db.execute('SELECT data FROM pipelines WHERE id=?', (recipe.id,)).fetchone()['data']
    history = {row['stage']: row['data'] for row in store.db.execute('SELECT stage,data FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,))}
    before = store.budget(owner, recipe.workflow_id)
    deadline = store.workflow(owner, recipe.workflow_id).deadline_at
    old = store.task(owner, p['child'].id)
    revised, arguments = revise_child(p, recipe)
    assert revised.graph_revision == 1
    assert Pipelines(store, dispatcher.root).replan(owner, recipe.id, **arguments) == revised
    assert store.db.execute('SELECT data FROM pipelines WHERE id=?', (recipe.id,)).fetchone()['data'] == base
    assert all(store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, key)).fetchone()['data'] == value
        for key, value in history.items())
    current = store.task(owner, old.id)
    assert current.id == old.id and current.semantic_revision == old.semantic_revision + 1
    assert current.acceptance == old.acceptance and current.permissions == old.permissions and current.budget == old.budget
    assert store.budget(owner, recipe.workflow_id) == before
    parent_response = model_server.respond
    calls = []
    def repair(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return parent_response(request)
        calls.append(request)
        assert current.objective in messages
        assert len(calls) == 1
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    model_server.respond = repair
    for number in range(25):
        dispatcher.tick(host, key=f'after-replan-{number}')
        wait_handles(dispatcher)
        assert store.workflow(owner, recipe.workflow_id).deadline_at == deadline
        if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
            break
    assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
    authors = [attempt for attempt in store.attempts(owner, recipe.workflow_id) if attempt.task_id == old.id]
    assert len(authors) == 2 and [attempt.state for attempt in authors] == ['interrupted', 'succeeded']
    assert authors[1].fence > authors[0].fence and authors[1].semantic_revision == current.semantic_revision
    ledger = store.budget(owner, recipe.workflow_id)
    assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain
    assert (p['source'] / 'src/parser.py').read_text() == 'value = 1\n'
    assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
    events = [event for event in store.events(owner, recipe.workflow_id) if event.kind == 'pipeline_replanned']
    assert len(events) == 1
    root = dispatcher.root
    retired = store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, 'graph:1')).fetchone()['data']
    rejected = json.loads(retired)['rejections'][0]
    paths = [root / 'artifacts' / rejected['artifact_id'] / 'manifest.json',
        root / 'verification' / store.db.execute("SELECT json_extract(data,'$.verdict.evidence_id') FROM reviews WHERE id=?", (rejected['review_id'],)).fetchone()[0] / 'manifest.json']
    for path in paths:
        raw = path.read_bytes()
        path.unlink()
        assert not Integration(store, root).current(owner, store.task(owner, p['parent'].id).result_id)
        path.write_bytes(raw)
        assert Integration(store, root).current(owner, store.task(owner, p['parent'].id).result_id)


def inert_state(p):
    store, owner = p['store'], p['owner']
    return (store.tasks(owner, p['workflow'].id), store.attempts(owner, p['workflow'].id),
        store.budget(owner, p['workflow'].id), store.outbox_count(),
        list(map(tuple, store.db.execute('SELECT * FROM pipeline_receipts ORDER BY stage'))))


@pytest.mark.parametrize('mutation', ['stale_workflow', 'stale_task', 'runtime', 'foreign', 'parent', 'criteria', 'budget',
    'permissions', 'empty', 'blank', 'cycle', 'foreign_edge', 'pause', 'cancel', 'unknown', 'unconfirmed',
    'live', 'deadline', 'source', 'inconclusive', 'execution', 'evidence'])
def test_replan_rejects_without_mutation(rejected_retry_boundary, mutation):  # noqa: F811
    from flycode.domain.orchestration import TaskCommand
    from flycode.domain.results import ReviewExecution, ReviewRecord
    from flycode.orchestration.store import StoreError, changed

    p, recipe = rejected_retry_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    task = store.task(owner, p['child'].id)
    arguments = {'changes': {task.id: {'objective': 'Repair parser from original base.'}},
        'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {item.id: item.revision for item in store.tasks(owner, recipe.workflow_id)}, 'key': 'denied-replan'}
    scope = owner
    if mutation == 'stale_workflow':
        arguments['expected_revision'] -= 1
    elif mutation == 'stale_task':
        arguments['expected_revisions'][task.id] -= 1
    elif mutation == 'runtime':
        scope = host
    elif mutation in {'foreign', 'parent'}:
        arguments['changes'] = {'foreign' if mutation == 'foreign' else p['parent'].id: {'objective': 'Cannot replace user goal'}}
    elif mutation in {'criteria', 'budget', 'permissions'}:
        arguments['changes'][task.id][{'criteria': 'acceptance', 'budget': 'budget', 'permissions': 'permissions'}[mutation]] = {}
    elif mutation == 'empty':
        arguments['changes'] = {}
    elif mutation == 'blank':
        arguments['changes'][task.id]['objective'] = ' '
    elif mutation in {'cycle', 'foreign_edge'}:
        arguments['changes'][task.id]['dependencies'] = (task.id if mutation == 'cycle' else 'foreign',)
    elif mutation in {'pause', 'cancel'}:
        store.command(owner, task.id, TaskCommand(operation=mutation, expected_revision=task.revision, idempotency_key='before-replan-' + mutation))
    elif mutation == 'unknown':
        store.db.execute('UPDATE reservations SET uncertain=1 WHERE attempt_id=?', (task.current_attempt_id,))
    elif mutation in {'unconfirmed', 'live'}:
        author = store._attempt(task.current_attempt_id)
        store._save_attempt(changed(author, **({'stop_confirmed': False} if mutation == 'unconfirmed' else {'state': 'running'})))
    elif mutation == 'deadline':
        workflow = store.workflow(owner, recipe.workflow_id)
        store.clock = lambda: workflow.deadline_at
    elif mutation == 'source':
        (p['source'] / 'dirty.bin').write_bytes(b'owner changed source')
    else:
        review = store._load('reviews', store.db.execute('SELECT id FROM reviews').fetchone()['id'], ReviewRecord)
        if mutation == 'inconclusive':
            review = changed(review, status='inconclusive', verdict=changed(review.verdict, status='inconclusive'))
            store.db.execute('UPDATE reviews SET data=? WHERE id=?', (review.model_dump_json(), review.id))
        elif mutation == 'execution':
            execution = store._load('review_executions', review.reviewer_attempt_id, ReviewExecution)
            execution = changed(execution, reruns=1)
            store.db.execute('UPDATE review_executions SET data=? WHERE id=?', (execution.model_dump_json(), execution.attempt_id))
        else:
            (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
    before = inert_state(p)
    with pytest.raises(StoreError):
        Pipelines(store, dispatcher.root).replan(scope, recipe.id, **arguments)
    assert inert_state(p) == before


def test_replan_rolls_back_outbox_failure_and_replays_after_restart(rejected_retry_boundary, monkeypatch):  # noqa: F811
    from flycode.orchestration.store import Store

    p, recipe = rejected_retry_boundary
    store, owner = p['store'], p['owner']
    original = store._event
    def unavailable(scope, workflow, kind, **values):
        if kind == 'pipeline_replanned':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **values)
    monkeypatch.setattr(store, '_event', unavailable)
    before = inert_state(p)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        revise_child(p, recipe)
    assert inert_state(p) == before
    monkeypatch.setattr(store, '_event', original)
    revised, arguments = revise_child(p, recipe)
    after = inert_state(p)
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        service = Pipelines(connection, p['dispatcher'].root)
        assert service.replan(owner, recipe.id, **arguments) == revised
        assert service.get(owner, recipe.id) == revised
        assert inert_state(p) == after
    finally:
        connection.close()


def test_two_connections_cannot_replace_a_graph_under_stale_revision(rejected_retry_boundary):  # noqa: F811
    from flycode.orchestration.store import Store, StoreError

    p, recipe = rejected_retry_boundary
    store, owner = p['store'], p['owner']
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        service = Pipelines(connection, p['dispatcher'].root)
        revised, arguments = revise_child(p, recipe)
        arguments['key'] = 'other-owner-replan'
        arguments['changes'][p['child'].id]['objective'] = 'A competing stale proposal'
        before = inert_state(p)
        with pytest.raises(StoreError, match='revision'):
            service.replan(owner, recipe.id, **arguments)
        assert service.get(owner, recipe.id) == revised and inert_state(p) == before
    finally:
        connection.close()


def test_graph_receipt_or_carried_checkpoint_tamper_revokes_dispatch(rejected_retry_boundary):  # noqa: F811
    from flycode.orchestration.store import StoreError

    p, recipe = rejected_retry_boundary
    revised, _ = revise_child(p, recipe)
    store, host = p['store'], p['host']
    service = Pipelines(store, p['dispatcher'].root)
    for stage in ('graph:1', 'wait:' + recipe.parent_id):
        raw = store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, stage)).fetchone()['data']
        mutated = json.loads(raw)
        mutated['created_at'] += 1
        store.db.execute('UPDATE pipeline_receipts SET data=? WHERE pipeline_id=? AND stage=?', (json.dumps(mutated), recipe.id, stage))
        with pytest.raises(StoreError, match='graph_changed'):
            service._guard(host, revised)
        store.db.execute('UPDATE pipeline_receipts SET data=? WHERE pipeline_id=? AND stage=?', (raw, recipe.id, stage))
    assert service._guard(host, revised)[0].state == 'running'


def test_replan_model_availability_preflight_never_holds_sql_writer_lock(rejected_retry_boundary, monkeypatch):  # noqa: F811
    p, recipe = rejected_retry_boundary
    store = p['store']
    original = store._binding_current
    calls = []
    def availability(profile):
        assert not store.db.in_transaction, 'Model admission filesystem read held the SQLite writer lock'
        calls.append(profile.id)
        return original(profile)
    monkeypatch.setattr(store, '_binding_current', availability)
    revise_child(p, recipe)
    assert calls


def test_replan_rechecks_exact_negative_execution_after_evidence_preflight(rejected_retry_boundary, monkeypatch):  # noqa: F811
    from flycode.domain.results import ReviewExecution
    from flycode.orchestration.replans import Replans
    from flycode.orchestration.store import StoreError, changed

    p, recipe = rejected_retry_boundary
    store = p['store']
    original = Replans.evidence
    def revoked(service, scope, saved, **values):
        result = original(service, scope, saved, **values)
        if not values.get('retired', True):
            execution = store._load('review_executions', saved.rejections[0].reviewer_attempt_id, ReviewExecution)
            execution = changed(execution, reruns=1)
            store.db.execute('UPDATE review_executions SET data=? WHERE id=?', (execution.model_dump_json(), execution.attempt_id))
        return result
    monkeypatch.setattr(Replans, 'evidence', revoked)
    before = store.tasks(p['owner'], recipe.workflow_id), store.budget(p['owner'], recipe.workflow_id), store.outbox_count()
    with pytest.raises(StoreError, match='retry_changed'):
        revise_child(p, recipe)
    assert (store.tasks(p['owner'], recipe.workflow_id), store.budget(p['owner'], recipe.workflow_id), store.outbox_count()) == before
    assert not store.db.execute("SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'graph:%'", (recipe.id,)).fetchone()


def test_owner_graph_versions_are_bounded_without_refunding_or_new_task_ids(rejected_retry_boundary):  # noqa: F811
    from flycode.orchestration.store import StoreError

    p, recipe = rejected_retry_boundary
    first, _ = revise_child(p, recipe)
    before = p['store'].budget(p['owner'], recipe.workflow_id)
    second, _ = revise_child(p, first, key='second-replan', changes={p['child'].id: {'objective': 'Second explicit repair objective.'}})
    assert second.graph_revision == 2
    assert set(second.tasks) == set(recipe.tasks)
    assert p['store'].budget(p['owner'], recipe.workflow_id) == before
    with pytest.raises(StoreError, match='replan_limit'):
        revise_child(p, second, key='third-replan', changes={p['child'].id: {'objective': 'Forbidden recursive graph expansion.'}})
    assert len(p['store'].attempts(p['owner'], recipe.workflow_id)) == 3


def test_pre_graph_retained_full_model_hash_runs_actual_review(review_ready, owned):  # noqa: F811
    import hashlib

    from flycode.domain.orchestration import TaskCommand
    from flycode.domain.results import PatchArtifact
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.store import changed
    from flycode.verification.snapshot import digest

    dispatcher, artifact, approval, task, *_ = review_ready
    store, owner, host, *_ = owned
    absent = {'prerequisites', 'continuation', 'retry', 'graph_input'}
    raw = artifact.model_dump_json(exclude={'patch_hash', *absent})
    legacy = artifact.model_dump(mode='json', exclude=absent)
    legacy['patch_hash'] = hashlib.sha256(raw.encode()).hexdigest()
    (Artifacts(store, dispatcher.root).directory / artifact.id / 'manifest.json').write_text(raw)
    store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(legacy), artifact.id))
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='pre-graph-patch', changes={'input_artifacts': {artifact.id: legacy['patch_hash']}}))
    binding = dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='pre-graph-review')
    binding = changed(binding, artifact_hash=digest(PatchArtifact.model_validate(legacy).model_dump(mode='json',
        exclude={'continuation', 'retry', 'graph_input'})))
    store.db.execute('UPDATE review_bindings SET data=? WHERE id=?', (binding.model_dump_json(), binding.id))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key='enqueue-pre-graph-review'))
    result = dispatcher.tick(host, key='run-pre-graph-review')
    assert result.attempt is not None, result.reasons
    assert store.task(owner, task.id).state == 'succeeded'


@pytest.fixture
def two_children_ready(retry_ready, model_server):  # noqa: F811
    from flycode.domain.orchestration import TaskCommand, TaskDraft
    from flycode.verification.engine import VerificationEngine

    p = retry_ready
    store, owner, dispatcher = p['store'], p['owner'], p['dispatcher']
    contract = p['child'].acceptance.model_copy(update={'id': 'second-child-acceptance'})
    draft = p['child'].model_dump(include=set(TaskDraft.model_fields))
    second = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({**draft,
        'title': 'Second child', 'objective': 'Set independent second value to 2.', 'dependencies': (p['child'].id,),
        'acceptance': contract, 'budget': BudgetAmount(model_calls=1, tool_calls=1)}), key='second-child')
    review = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
        **p['child_review'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Second review',
        'acceptance': contract, 'budget': BudgetAmount(tool_calls=4)}), key='second-review')
    integrator = p['integrator']
    p['integrator'] = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='second-integrator', changes={'dependencies': (*integrator.dependencies, review.id),
            'budget': BudgetAmount(tool_calls=35).model_dump()}))
    (p['source'] / 'src/second.py').write_text('value = 0\n')
    oracle = p['source'].parent / 'second-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n    assert runpy.run_path("src/second.py")["value"] == 2\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    approval = engine.oracles.approve(contract, engine.oracles.read(p['approvals'][p['child'].id]).checks, oracle.parent)
    p['approvals'].update({second.id: approval, review.id: approval})
    for task in (p['parent'], p['child'], second):
        previous = dispatcher.binding(owner, task.id) if task.id != second.id else None
        dispatcher.bind(owner, task.id, p['source'], expected_revision=task.revision,
            expected_binding_id=previous.id if previous else None, key='two-child-bind-' + task.id, brain='heuristic_fsm')
    p.update(second=second, second_review=review)
    return p


def test_second_graph_revision_carries_new_completion_and_changes_pending_dependency(two_children_ready, model_server):
    p = two_children_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    service = Pipelines(store, dispatcher.root)
    recipe = service.approve_parent(owner, p['workflow'].id,
        {p['parent'].id: p['parent_review'].id, p['child'].id: p['child_review'].id, p['second'].id: p['second_review'].id},
        p['integrator'].id, source=p['source'], expected_revisions={task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
        approval_hashes=p['approvals'], key='approve-two-children', preparation_steps=1, retry_policies=p['retry_policies'])
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'two-before-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'failed':
                break
        assert store.task(owner, p['child_review'].id).state == 'failed'
        deadline = store.workflow(owner, recipe.workflow_id).deadline_at
        first, _ = revise_child(p, recipe)
        parent_response = model_server.respond
        def repair(request):
            messages = str(request['messages'])
            if p['parent'].objective in messages:
                return parent_response(request)
            second = 'Second explicit child objective.' in messages
            arguments = {'path': 'src/second.py' if second else 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 2' if second else 'value = 1'}
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
        model_server.respond = repair
        for number in range(12):
            dispatcher.tick(host, key=f'two-first-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'succeeded':
                break
        assert service.advance(host, recipe.workflow_id) == ()
        assert store.task(owner, p['child'].id).state == 'succeeded'
        assert service.advance(host, recipe.workflow_id) == ()
        assert store.task(owner, p['second'].id).state == 'ready'
        before = store.budget(owner, recipe.workflow_id)
        second, _ = revise_child(p, first, key='replan-pending-second', changes={p['second'].id: {
            'objective': 'Second explicit child objective.', 'dependencies': ()}})
        assert second.graph_revision == 2 and store.budget(owner, recipe.workflow_id) == before
        for number in range(25):
            dispatcher.tick(host, key=f'two-final-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
                break
        assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
        assert store.workflow(owner, recipe.workflow_id).deadline_at == deadline
        assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
        assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
        assert len(store.attempts(owner, recipe.workflow_id)) == 10
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_simultaneous_owner_preflights_publish_one_graph_revision(rejected_retry_boundary, monkeypatch):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.replans import Replans
    from flycode.orchestration.store import Store, StoreError

    p, recipe = rejected_retry_boundary
    store, owner = p['store'], p['owner']
    arguments = {'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)}}
    barrier, original = Barrier(2), Replans.evidence
    def both_ready(service, scope, saved, **values):
        result = original(service, scope, saved, **values)
        if not values.get('retired', True):
            barrier.wait(10)
        return result
    monkeypatch.setattr(Replans, 'evidence', both_ready)
    def command(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Pipelines(connection, p['dispatcher'].root).replan(owner, recipe.id,
                key=f'concurrent-graph-{number}', changes={p['child'].id: {'objective': f'Repair parser under proposal {number}.'}}, **arguments)
        except StoreError as error:
            return error.code
        finally:
            connection.close()
    before = store.budget(owner, recipe.workflow_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(command, [1, 2]))
    assert sum(hasattr(result, 'graph_revision') for result in results) == 1
    assert [result for result in results if isinstance(result, str)] == ['revision']
    assert store.budget(owner, recipe.workflow_id) == before
    assert len([event for event in store.events(owner, recipe.workflow_id) if event.kind == 'pipeline_replanned']) == 1


def test_revoked_graph_evidence_during_actual_http_discards_paid_candidate(rejected_retry_boundary, model_server):  # noqa: F811
    from flycode.orchestration.artifacts import Artifacts

    p, recipe = rejected_retry_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    revise_child(p, recipe)
    saved = json.loads(store.db.execute("SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage='graph:1'", (recipe.id,)).fetchone()['data'])
    path = Artifacts(store, dispatcher.root).directory / saved['rejections'][0]['artifact_id'] / 'manifest.json'
    def revoke(request):
        path.unlink()
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    model_server.respond = revoke
    before = store.budget(owner, recipe.workflow_id)
    result = dispatcher.tick(host, key='replan-revoked-after-http')
    assert result.attempt is not None and result.attempt.task_id == p['child'].id
    handle = dispatcher.handles[result.attempt.id]
    assert handle.finished.wait(25) and handle.process_stopped.is_set()
    ledger = store.budget(owner, recipe.workflow_id)
    assert ledger.spent.model_calls == before.spent.model_calls + 1
    assert ledger.spent.tool_calls == before.spent.tool_calls and ledger.reserved == BudgetAmount() and not ledger.uncertain
    repository = dispatcher.runtime.workspaces.repository(owner, result.attempt.id)
    assert (repository / 'src/parser.py').read_text() == 'value = 0\n'
    assert not store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (result.attempt.id,)).fetchone()


def test_replan_of_pending_child_preserves_and_retires_carried_failed_review(two_children_ready, model_server):
    p = two_children_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    service = Pipelines(store, dispatcher.root)
    recipe = service.approve_parent(owner, p['workflow'].id,
        {p['parent'].id: p['parent_review'].id, p['child'].id: p['child_review'].id, p['second'].id: p['second_review'].id},
        p['integrator'].id, source=p['source'], expected_revisions={task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
        approval_hashes=p['approvals'], key='approve-carried-failure', preparation_steps=1, retry_policies=p['retry_policies'])
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'carried-before-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'failed':
                break
        assert store.task(owner, p['child_review'].id).state == 'failed'
        stage = service._review_stage(recipe, next(branch for branch in recipe.branches if branch.writer_id == p['child'].id))
        previous = store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, stage)).fetchone()['data']
        revised, _ = revise_child(p, recipe, changes={p['second'].id: {'objective': 'Replanned second child.', 'dependencies': ()}})
        assert service.advance(host, recipe.workflow_id) == ()
        assert store.task(owner, p['child_review'].id).state == 'planned'
        assert stage not in service._guard(host, revised)[2]
        assert store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, stage)).fetchone()['data'] == previous
        original = model_server.respond
        def respond(request):
            if 'Replanned second child.' not in str(request['messages']):
                return original(request)
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
                'path': 'src/second.py', 'old_text': 'value = 0', 'new_text': 'value = 2'}}]}))
        model_server.respond = respond
        for number in range(30):
            dispatcher.tick(host, key=f'carried-final-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
                break
        assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
        assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
        assert len(store.attempts(owner, recipe.workflow_id)) == 10
    finally:
        for handle in dispatcher.handles.values():
            handle.close()
