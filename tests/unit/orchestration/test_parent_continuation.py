"""A stopped parent resumes exact partial work only after verified children."""

import json

import pytest
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_review_dispatch import review_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import BudgetAmount, TaskCommand, TaskDraft
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def parent_ready(pipeline_ready, model_server):  # noqa: F811
    store, owner, host, workflow, parent, child, child_review, integrator, dispatcher, source, child_approval = pipeline_ready
    def revise(task, key, **values):
        return store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key=key, changes=values))
    contract = parent.acceptance.model_copy(update={'id': 'parent-acceptance'})
    parent = revise(parent, 'parent-contract', acceptance=contract.model_dump(mode='json'),
        objective='Prepare a partial marker, wait for child repair, then set parent value to 4.',
        budget=BudgetAmount(model_calls=2, tool_calls=2).model_dump(), allowed_profile_ids=child.allowed_profile_ids)
    child = revise(child, 'child-contract', objective='Set child parser value to 1.',
        budget=BudgetAmount(model_calls=1, tool_calls=1).model_dump())
    parent_review = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **child_review.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Review parent continuation',
        'acceptance': contract}), key='parent-review')
    integrator = revise(integrator, 'parent-integrator', acceptance=contract.model_dump(mode='json'),
        dependencies=(child_review.id, parent_review.id))
    (source / 'src/extra.py').write_text('value = 0\n')
    (source / 'src/note.py').write_text('phase = 0\n')
    oracle = source.parent / 'parent-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n'
        '    assert runpy.run_path("src/parser.py")["value"] == 1\n'
        '    assert runpy.run_path("src/note.py")["phase"] == 1\n'
        '    assert runpy.run_path("src/extra.py")["value"] == 4\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    parent_approval = engine.oracles.approve(contract, engine.oracles.read(child_approval).checks, oracle.parent)
    for task in (parent, child):
        previous = dispatcher.binding(owner, task.id) if task.id == child.id else None
        dispatcher.bind(owner, task.id, source, expected_revision=task.revision,
            expected_binding_id=previous.id if previous else None, key='parent-bind-' + task.id, brain='heuristic_fsm')
    parent_calls = []
    def response(request):
        messages = str(request['messages'])
        if parent.objective in messages:
            parent_calls.append(request)
            assert len(parent_calls) <= 2, 'Parent inference exceeded both approved stages'
            arguments = {'path': 'src/note.py', 'old_text': 'phase = 0', 'new_text': 'phase = 1'} if len(parent_calls) == 1 else {
                'path': 'src/extra.py', 'old_text': 'value = 0', 'new_text': 'value = 4'}
        else:
            assert child.objective in messages
            arguments = {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': arguments}]}))
    model_server.respond = response
    return {'store': store, 'owner': owner, 'host': host, 'workflow': workflow, 'parent': parent, 'child': child,
        'parent_review': parent_review, 'child_review': child_review, 'integrator': integrator, 'dispatcher': dispatcher,
        'source': source, 'approvals': {parent.id: parent_approval, parent_review.id: parent_approval,
            integrator.id: parent_approval, child.id: child_approval, child_review.id: child_approval}}


def approve_parent(prepared, **changes):
    p = prepared
    service = Pipelines(p['store'], p['dispatcher'].root)
    assert hasattr(service, 'approve_parent'), 'Explicit resumable-parent authority is missing'
    values = {'source': p['source'], 'expected_revisions': {task.id: task.revision
        for task in p['store'].tasks(p['owner'], p['workflow'].id)},
        'approval_hashes': p['approvals'], 'key': 'approve-parent-continuation', 'preparation_steps': 1}
    values.update(changes)
    return service.approve_parent(p['owner'], p['workflow'].id,
        {p['parent'].id: p['parent_review'].id, p['child'].id: p['child_review'].id}, p['integrator'].id, **values)


def wait_handles(dispatcher):
    for handle in dispatcher.handles.values():
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()


def test_parent_releases_slot_restores_partial_bytes_and_resumes_after_exact_child_completion(parent_ready, monkeypatch):
    p = parent_ready
    store, owner, host, dispatcher, source = (p[key] for key in ('store', 'owner', 'host', 'dispatcher', 'source'))
    workflow, parent, child = (p[key] for key in ('workflow', 'parent', 'child'))
    before = store.budget(owner, workflow.id)
    revisions = {task.id: task.revision for task in store.tasks(owner, workflow.id)}
    recipe = approve_parent(p, expected_revisions=revisions)
    assert recipe.version == 4 and approve_parent(p, expected_revisions=revisions) == recipe
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Parent route used manual claims'))
    monkeypatch.setattr(store, 'command', lambda *a, **kw: pytest.fail('Parent route required another owner command'))
    try:
        first = dispatcher.tick(host, key='prepare-parent')
        assert first.attempt is not None and first.attempt.task_id == parent.id
        wait_handles(dispatcher)
        deadline = store.workflow(owner, workflow.id).deadline_at
        partial = Artifacts(store, dispatcher.root).for_attempt(owner, first.attempt.id)
        assert (dispatcher.runtime.workspaces.repository(owner, first.attempt.id) / 'src/note.py').read_text() == 'phase = 1\n'
        assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ()
        suspended = store.task(owner, parent.id)
        assert suspended.state == 'blocked' and suspended.blocked_reason == 'waiting_children'
        assert suspended.current_attempt_id is None and suspended.completion_id is None
        assert store._attempt(first.attempt.id).state == 'interrupted' and store._attempt(first.attempt.id).stop_confirmed
        assert store.instance(host, first.attempt.instance_id).state == 'idle'
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent == BudgetAmount(model_calls=1, tool_calls=1) and ledger.reserved == BudgetAmount()
        assert (source / 'src/note.py').read_text() == 'phase = 0\n'
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        for number in range(20):
            dispatcher.tick(host, key=f'continue-parent-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, workflow.id).state == 'succeeded':
                break
        assert store.workflow(owner, workflow.id).state == 'succeeded'
        assert store.workflow(owner, workflow.id).deadline_at == deadline
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 6
        parents = [attempt for attempt in attempts if attempt.task_id == parent.id]
        assert len(parents) == 2 and parents[1].fence > parents[0].fence
        assert parents[0].state == 'interrupted' and parents[1].state == 'succeeded'
        proof = store.task(owner, child.id).completion_id
        assert proof is not None and parents[1].prerequisites[child.id].id == proof
        assert parents[1].initial_budget == BudgetAmount(model_calls=1, tool_calls=1)
        assert sum(attempt.spent.model_calls for attempt in parents) == 2
        assert sum(attempt.spent.tool_calls for attempt in parents) == 2
        child_attempt = next(attempt for attempt in attempts if attempt.task_id == child.id)
        child_checkout = dispatcher.runtime.workspaces.repository(owner, child_attempt.id)
        assert (child_checkout / 'src/note.py').read_text() == 'phase = 0\n'
        assert Artifacts(store, dispatcher.root).get(owner, partial.id) == partial
        assert (source / 'src/note.py').read_text() == 'phase = 1\n'
        assert (source / 'src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'src/extra.py').read_text() == 'value = 4\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        record = store.db.execute("SELECT id FROM integrations WHERE state='completed'").fetchone()
        assert record is not None and Integration(store, dispatcher.root).current(owner, record['id'])
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 3 and ledger.reserved == BudgetAmount() and not ledger.uncertain
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_lost_parent_checkpoint_invalidates_the_completed_workflow(parent_ready, monkeypatch):
    test_parent_releases_slot_restores_partial_bytes_and_resumes_after_exact_child_completion(parent_ready, monkeypatch)
    p = parent_ready
    store, owner, dispatcher = (p[key] for key in ('store', 'owner', 'dispatcher'))
    first = next(attempt for attempt in store.attempts(owner, p['workflow'].id) if attempt.task_id == p['parent'].id)
    checkpoint = Artifacts(store, dispatcher.root).for_attempt(owner, first.id)
    result_id = store.task(owner, p['parent'].id).result_id
    before = store.budget(owner, p['workflow'].id)
    (Artifacts(store, dispatcher.root).directory / checkpoint.id / 'manifest.json').unlink()
    assert not Integration(store, dispatcher.root).current(owner, result_id)
    assert Integration(store, dispatcher.root).refresh(owner, result_id).state == 'invalidated'
    assert store.budget(owner, p['workflow'].id).spent == before.spent
    assert (p['source'] / 'src/note.py').read_text() == 'phase = 1\n'


def test_failed_child_review_blocks_parent_with_a_durable_reason(parent_ready, model_server):
    p = parent_ready
    original = model_server.respond
    def fail_child(request):
        if p['child'].objective not in str(request['messages']):
            return original(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'}}]}))
    model_server.respond = fail_child
    approve_parent(p)
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'failed-child-{number}')
            wait_handles(dispatcher)
        parent = store.task(owner, p['parent'].id)
        assert parent.state == 'blocked' and parent.blocked_reason == 'child_review_failed'
        assert store.task(owner, p['child'].id).completion_id is None
        attempts = store.attempts(owner, p['workflow'].id)
        assert len([attempt for attempt in attempts if attempt.task_id == parent.id]) == 1
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent.model_calls == 2 and ledger.reserved == BudgetAmount() and not ledger.uncertain
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.fixture(params=['suspend', 'resume'])
def parent_boundary(parent_ready, request):
    p = parent_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    recipe = approve_parent(p)
    first = dispatcher.tick(host, key='boundary-prepare')
    assert first.attempt is not None and first.attempt.task_id == p['parent'].id
    wait_handles(dispatcher)
    try:
        if request.param == 'resume':
            assert Pipelines(store, dispatcher.root).advance(host, p['workflow'].id) == ()
            for number in range(8):
                dispatcher.tick(host, key=f'boundary-child-{number}')
                wait_handles(dispatcher)
                if store.task(owner, p['child_review'].id).state == 'succeeded':
                    break
            assert store.task(owner, p['child_review'].id).state == 'succeeded'
            assert Pipelines(store, dispatcher.root).advance(host, p['workflow'].id) == ()
            assert store.task(owner, p['child'].id).state == 'succeeded'
            assert store.task(owner, p['parent'].id).state == 'blocked'
        yield p, recipe, request.param
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_parent_transition_is_atomic_across_two_connections(parent_boundary, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration import continuations
    from flycode.orchestration.store import Store

    p, recipe, stage = parent_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    barrier = Barrier(2)
    if stage == 'suspend':
        original = Pipelines._artifact
        def synchronize(service, *args, **kwargs):
            result = original(service, *args, **kwargs)
            barrier.wait(timeout=10)
            return result
        monkeypatch.setattr(Pipelines, '_artifact', synchronize)
    else:
        original = continuations.capture
        def synchronize(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == p['source']:
                barrier.wait(timeout=10)
            return result
        monkeypatch.setattr(continuations, 'capture', synchronize)
    before = store.budget(owner, p['workflow'].id)
    def advance(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Pipelines(connection, dispatcher.root).advance(host, p['workflow'].id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(advance, range(2))) == [(), ()]
    prefix = 'wait:' if stage == 'suspend' else 'writer:'
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
        (recipe.id, prefix + p['parent'].id)).fetchone()[0] == 1
    kind = 'parent_suspended' if stage == 'suspend' else 'parent_continuation_ready'
    assert len([event for event in store.events(owner, p['workflow'].id) if event.kind == kind]) == 1
    assert store.budget(owner, p['workflow'].id) == before
    assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'


def test_parent_transition_rolls_back_outbox_failure_and_replays_after_restart(parent_boundary, monkeypatch):
    from flycode.orchestration.store import Store

    p, recipe, stage = parent_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    before = store.task(owner, p['parent'].id), store.attempts(owner, p['workflow'].id), store.budget(owner, p['workflow'].id)
    prefix = 'wait:' if stage == 'suspend' else 'writer:'
    kind = 'parent_suspended' if stage == 'suspend' else 'parent_continuation_ready'
    original = store._event
    def fail(scope, workflow, event_kind, **kwargs):
        if event_kind == kind:
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, event_kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        Pipelines(store, dispatcher.root).advance(host, p['workflow'].id)
    assert (store.task(owner, p['parent'].id), store.attempts(owner, p['workflow'].id), store.budget(owner, p['workflow'].id)) == before
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
        (recipe.id, prefix + p['parent'].id)).fetchone()
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        service = Pipelines(connection, dispatcher.root)
        assert service.advance(host, p['workflow'].id) == ()
        assert service.advance(host, p['workflow'].id) == ()
    finally:
        connection.close()
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
        (recipe.id, prefix + p['parent'].id)).fetchone()[0] == 1
    assert len([event for event in store.events(owner, p['workflow'].id) if event.kind == kind]) == 1
    assert store.budget(owner, p['workflow'].id) == before[2]


def test_child_cannot_depend_on_its_suspended_parent(parent_ready):
    from flycode.orchestration.store import StoreError

    p = parent_ready
    store, owner = p['store'], p['owner']
    before = store.budget(owner, p['workflow'].id)
    with pytest.raises(StoreError, match='cycle'):
        store.command(owner, p['child'].id, TaskCommand(operation='revise', expected_revision=p['child'].revision,
            idempotency_key='child-waits-parent', changes={'dependencies': (p['parent'].id,)}))
    assert not store.db.execute('SELECT 1 FROM pipelines').fetchone()
    assert store.attempts(owner, p['workflow'].id) == [] and store.budget(owner, p['workflow'].id) == before


@pytest.mark.parametrize('change', ['deadline', 'clock_rollback', 'pause', 'cancel'])
def test_parent_transition_cannot_outlive_its_authority(parent_boundary, change):
    p, recipe, stage = parent_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    workflow = store.workflow(owner, p['workflow'].id)
    parent = store.task(owner, p['parent'].id)
    if change in {'deadline', 'clock_rollback'}:
        store.clock = lambda: workflow.deadline_at + 1 if change == 'deadline' else workflow.started_at - 1
    else:
        store.command(owner, parent.id, TaskCommand(operation=change, expected_revision=parent.revision,
            idempotency_key='revoke-parent-' + change))
    before = store.budget(owner, workflow.id)
    result = Pipelines(store, dispatcher.root).advance(host, workflow.id)
    assert result, 'Revoked parent continuation was silently accepted'
    prefix = 'wait:' if stage == 'suspend' else 'writer:'
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
        (recipe.id, prefix + parent.id)).fetchone()
    assert store.budget(owner, workflow.id).spent == before.spent
    assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('change', ['child_objective', 'new_child'])
def test_stale_parent_delegation_blocks_all_canonical_claim_routes(parent_ready, automatic, change):
    from flycode.domain.orchestration import AutoClaimRequest, ClaimRequest

    p = parent_ready
    store, owner, host = p['store'], p['owner'], p['host']
    approve_parent(p)
    if change == 'child_objective':
        child = store.task(owner, p['child'].id)
        store.command(owner, child.id, TaskCommand(operation='revise', expected_revision=child.revision,
            idempotency_key='replace-child-objective', changes={'objective': 'A different owner-approved child goal.'}))
    else:
        store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
            **p['child'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Additional mandatory child'}),
            key='additional-parent-child')
    store.start_next(host, key='start-stale-parent')
    parent = store.task(owner, p['parent'].id)
    instance = next(item for item in store.instances(host) if item.profile_id in parent.allowed_profile_ids and item.state == 'idle')
    before = store.budget(owner, p['workflow'].id)
    common = {'instance_id': instance.id, 'expected_instance_revision': instance.revision, 'idempotency_key': 'claim-stale-parent'}
    if automatic:
        result = store.claim_next(host, AutoClaimRequest(**common))
    else:
        result = store.claim(host, ClaimRequest(**common, task_id=parent.id, expected_task_revision=parent.revision, reserve=parent.budget))
    assert result.attempt is None, 'Changed owner graph reused old parent execution authority'
    assert store.attempts(owner, p['workflow'].id) == [] and store.budget(owner, p['workflow'].id) == before


@pytest.mark.parametrize('role', ['reader', 'owner'])
def test_continuation_mutation_requires_runtime_scope(parent_boundary, role):
    from flycode.orchestration.continuations import Continuations
    from flycode.orchestration.store import StoreError

    p, recipe, _ = parent_boundary
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    service = Pipelines(store, dispatcher.root)
    _, tasks, receipts = service._guard(host, recipe)
    before = store.tasks(host, p['workflow'].id), store.budget(host, p['workflow'].id), store.outbox_count()
    with pytest.raises(StoreError, match='scope'):
        Continuations(store, dispatcher.root).advance(host.model_copy(update={'role': role}), service, recipe, tasks, receipts)
    assert (store.tasks(host, p['workflow'].id), store.budget(host, p['workflow'].id), store.outbox_count()) == before


def test_host_drain_after_checkpoint_preflight_blocks_parent_transition(parent_boundary, monkeypatch):
    from flycode.orchestration.host import Host

    p, recipe, stage = parent_boundary
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(p['source'].parent / 'host-authority'))
    original = Artifacts.get
    with Host(store.path, p['source'].parent / 'host-state') as leader:
        managed = leader.connect(catalogue=store.catalogue)
        try:
            service = Pipelines(managed, dispatcher.root)
            drained = []
            def revoke(artifacts, *args, **kwargs):
                result = original(artifacts, *args, **kwargs)
                if result.task_id == p['parent'].id and not drained:
                    assert not managed.db.in_transaction
                    drained.append(True)
                    leader.drain()
                return result
            monkeypatch.setattr(Artifacts, 'get', revoke)
            before = managed.budget(owner, p['workflow'].id)
            reasons = service.advance(host, p['workflow'].id)
            assert drained and any(reason.startswith('host_') for reason in reasons)
            prefix = 'wait:' if stage == 'suspend' else 'writer:'
            assert not managed.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
                (recipe.id, prefix + p['parent'].id)).fetchone()
            assert managed.budget(owner, p['workflow'].id).spent == before.spent
        finally:
            managed.close()


def test_persisted_review_binding_retains_its_pre_lineage_artifact_hash(review_ready, owned):  # noqa: F811
    import hashlib

    from flycode.domain.results import PatchArtifact
    from flycode.orchestration.store import changed
    from flycode.verification.snapshot import digest

    dispatcher, artifact, approval, task, *_ = review_ready
    store, owner, host, workflow, *_ = owned
    raw = artifact.model_dump_json(exclude={'patch_hash', 'prerequisites', 'continuation'})
    legacy = artifact.model_dump(mode='json', exclude={'prerequisites', 'continuation'})
    legacy['patch_hash'] = hashlib.sha256(raw.encode()).hexdigest()
    (Artifacts(store, dispatcher.root).directory / artifact.id / 'manifest.json').write_text(raw)
    store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(legacy), artifact.id))
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='old-patch-input', changes={'input_artifacts': {artifact.id: legacy['patch_hash']}}))
    binding = dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approval,
        expected_revision=task.revision, key='legacy-review')
    binding = changed(binding, artifact_hash=digest(PatchArtifact.model_validate(legacy).model_dump(mode='json', exclude={'continuation'})))
    store.db.execute('UPDATE review_bindings SET data=? WHERE id=?', (binding.model_dump_json(), binding.id))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key='enqueue-old-review'))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='run-old-review')
    assert result.attempt is not None, result.reasons
    assert store.task(owner, task.id).state == 'succeeded'
    assert store.budget(owner, workflow.id).spent.tool_calls == before.spent.tool_calls + 4



def test_parent_and_child_can_resume_identical_file_deletions(parent_ready, model_server, monkeypatch):
    p = parent_ready
    source = p['source']
    (source / 'src/remove_me.txt').write_bytes(b'remove this exact owner-approved input\n')
    (source / 'tests/test_delete_effects.py').write_text(
        'from pathlib import Path\n'
        'def test_prepare():\n'
        '    Path("src/remove_me.txt").unlink()\n'
        '    Path("src/note.py").write_text("phase = 1\\n")\n'
        'def test_child():\n'
        '    Path("src/remove_me.txt").unlink()\n'
        '    Path("src/parser.py").write_text("value = 1\\n")\n')
    for task in (p['parent'], p['child']):
        previous = p['dispatcher'].binding(p['owner'], task.id)
        p['dispatcher'].bind(p['owner'], task.id, source, expected_revision=task.revision,
            expected_binding_id=previous.id, key='bind-identical-deletion-' + task.id, brain='heuristic_fsm')
    parent_calls = []
    def respond(request):
        if p['parent'].objective in str(request['messages']):
            parent_calls.append(request)
            candidate = {'tool': 'run_tests', 'arguments': {'target': 'tests/test_delete_effects.py::test_prepare'}} if len(parent_calls) == 1 else {
                'tool': 'edit_file', 'arguments': {'path': 'src/extra.py', 'old_text': 'value = 0', 'new_text': 'value = 4'}}
        else:
            assert p['child'].objective in str(request['messages'])
            candidate = {'tool': 'run_tests', 'arguments': {'target': 'tests/test_delete_effects.py::test_child'}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))
    model_server.respond = respond
    test_parent_releases_slot_restores_partial_bytes_and_resumes_after_exact_child_completion(p, monkeypatch)
    assert not (source / 'src/remove_me.txt').exists()


@pytest.mark.parametrize('boundary', ['launch', 'publish'])
def test_child_revoke_after_preflight_blocks_parent_commit(parent_ready, monkeypatch, boundary):
    from flycode.orchestration.composition import Compositions
    from flycode.orchestration.continuations import Continuations

    p = parent_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p)
    changed_child = []
    def revoke(connection):
        child = connection.task(owner, p['child'].id)
        connection.command(owner, child.id, TaskCommand(operation='revise', expected_revision=child.revision,
            idempotency_key='revoke-child-after-preflight', changes={'objective': 'Revised mandatory child objective.'}))
        changed_child.append(True)
    if boundary == 'launch':
        original = Continuations.configure
        def after_preflight(service, *args, **kwargs):
            result = original(service, *args, **kwargs)
            revoke(service.store)
            return result
        monkeypatch.setattr(Continuations, 'configure', after_preflight)
    else:
        original = Compositions.stable
        def after_preflight(service, scope, attempt, evidence):
            result = original(service, scope, attempt, evidence)
            if attempt.task_id == p['parent'].id and not changed_child:
                revoke(service.store)
            return result
        monkeypatch.setattr(Compositions, 'stable', after_preflight)
    try:
        dispatcher.tick(host, key='parent-preflight-race')
        for handle in dispatcher.handles.values():
            assert handle.finished.wait(25)
            assert handle.process_stopped.is_set()
        assert changed_child
        if boundary == 'launch':
            assert not store.db.execute('SELECT 1 FROM workers').fetchone()
            assert store.budget(owner, p['workflow'].id).spent == BudgetAmount()
        else:
            assert not store.db.execute('SELECT 1 FROM artifacts').fetchone()
            assert store.budget(owner, p['workflow'].id).spent == BudgetAmount(model_calls=1, tool_calls=1)
        assert not store.db.execute('SELECT 1 FROM pipeline_receipts').fetchone()
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_parent_transition_does_not_resolve_model_paths_under_writer_lock(parent_boundary, monkeypatch):
    p, _, _ = parent_boundary
    store, host, dispatcher = p['store'], p['host'], p['dispatcher']
    original = store._binding_current
    def resolve(profile):
        assert not store.db.in_transaction, 'Model catalogue resolution reached the continuation writer lock'
        return original(profile)
    monkeypatch.setattr(store, '_binding_current', resolve)
    assert Pipelines(store, dispatcher.root).advance(host, p['workflow'].id) == ()


@pytest.mark.parametrize('kind', ['model', 'tool'])
def test_graph_revocation_retains_paid_outcomes_and_releases_stopped_parent(parent_ready, monkeypatch, kind):
    from threading import Event

    from flycode.orchestration.resources import Resources
    from flycode.orchestration.tools import Tools

    p = parent_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p)
    reached, release = Event(), Event()
    service_type = Resources if kind == 'model' else Tools
    original = service_type.finish
    def hold_ack(service, scope, identity, **kwargs):
        call = service.get(scope, identity)
        if call.task_id == p['parent'].id and kwargs['status'] == 'completed' and not reached.is_set():
            reached.set()
            assert release.wait(15), 'Owner did not release paid-outcome acknowledgement'
        return original(service, scope, identity, **kwargs)
    monkeypatch.setattr(service_type, 'finish', hold_ack)
    try:
        result = dispatcher.tick(host, key='parent-paid-outcome')
        assert result.attempt is not None and reached.wait(15)
        child = store.task(owner, p['child'].id)
        store.command(owner, child.id, TaskCommand(operation='revise', expected_revision=child.revision,
            idempotency_key='revoke-child-during-paid-outcome', changes={'objective': 'Revised child while paid work settles.'}))
        release.set()
        handle = dispatcher.handles[result.attempt.id]
        assert handle.finished.wait(25) and handle.process_stopped.is_set(), handle.error
        records = service_type(store).calls(owner, p['workflow'].id)
        assert len(records) == 1 and records[0].status == 'completed' and records[0].late
        attempt = store._attempt(result.attempt.id)
        assert attempt.state == 'failed' and attempt.stop_confirmed
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent == BudgetAmount(model_calls=1, tool_calls=int(kind == 'tool'))
        assert ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert not store.db.execute('SELECT 1 FROM artifacts').fetchone()
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'
    finally:
        release.set()
        for handle in dispatcher.handles.values():
            handle.close()
