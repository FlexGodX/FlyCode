"""A preapproved task DAG advances only through checked predecessor receipts."""

import json
import re

import pytest
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import (
    AgentProfileDraft,
    AutoClaimRequest,
    BudgetAmount,
    ClaimRequest,
    TaskCommand,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.completions import Completions
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import Store, StoreError
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def dependency_recipe_ready(pipeline_ready, model_server):  # noqa: F811
    store, owner, host, workflow, root, first, check, integrator, dispatcher, source, old_approval = pipeline_ready
    def revise(task, key, **changes):
        task = store.task(owner, task.id)
        return store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key=key, changes=changes))
    first = revise(first, 'first-dag-criteria',
        objective='Set checked chain value to 1.', budget=BudgetAmount(model_calls=1, tool_calls=1).model_dump())
    writers, reviewers = [first], [check]
    for number in (2, 3):
        writer = store.create_task(owner, workflow.id, TaskDraft.model_validate({
            **first.model_dump(include=set(TaskDraft.model_fields)), 'title': f'Checked chain writer {number}',
            'objective': f'Set checked chain value to {number}.',
            'acceptance': root.acceptance.model_copy(update={'id': f'dag-{number}'}),
            'dependencies': (first.id,) if number == 2 else tuple(item.id for item in writers)}), key=f'dag-writer-{number}')
        reviewer = store.create_task(owner, workflow.id, TaskDraft.model_validate({
            **check.model_dump(include=set(TaskDraft.model_fields)), 'title': f'Review checked chain {number}',
            'acceptance': writer.acceptance}), key=f'dag-review-{number}')
        writers.append(writer)
        reviewers.append(reviewer)
    integrator = revise(integrator, 'dag-integrator', dependencies=tuple(item.id for item in reviewers),
        budget=BudgetAmount(tool_calls=45).model_dump())
    template = 'import runpy\ndef test_parse():\n    assert runpy.run_path("src/parser.py")["value"] {comparison}\n'
    (source / 'tests/test_parser.py').write_text(template.format(comparison='>= 1'))
    engine = VerificationEngine(dispatcher.root / 'verification')
    checks = engine.oracles.read(old_approval).checks
    approvals = {}
    for number, task in ((0, root), (2, writers[1]), (3, writers[2])):
        directory = source.parent / f'dag-oracle-{number}/tests'
        directory.mkdir(parents=True)
        comparison = '>= 1' if number <= 1 else '>= 2' if number == 2 else '== 3'
        (directory / 'test_parser.py').write_text(template.format(comparison=comparison))
        approvals[task.id] = engine.oracles.approve(task.acceptance, checks, directory.parent)
    approvals[first.id] = approvals[root.id]
    for writer, reviewer in zip(writers, reviewers):
        approvals[reviewer.id] = approvals[writer.id]
    approvals[integrator.id] = approvals[root.id]
    for writer in writers:
        previous = dispatcher.binding(owner, writer.id) if writer.id == first.id else None
        dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision,
            expected_binding_id=previous.id if previous else None, key='dag-bind-' + writer.id, brain='heuristic_fsm')
    def response(request):
        match = re.search(r'Set checked chain value to ([123])\.', str(request['messages']))
        assert match is not None, request
        value = int(match.group(1))
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': f'value = {value - 1}', 'new_text': f'value = {value}'}}]}))
    model_server.respond = response
    return store, owner, host, workflow, root, writers, reviewers, integrator, dispatcher, source, approvals


def approve_dependency_recipe(prepared, **changes):
    store, owner, _, workflow, _, writers, reviewers, integrator, dispatcher, source, approvals = prepared
    service = Pipelines(store, dispatcher.root)
    assert hasattr(service, 'approve_dag'), 'Owner-approved dependency recipe is missing'
    values = {'source': source, 'expected_revisions': {task.id: task.revision for task in store.tasks(owner, workflow.id)},
        'approval_hashes': approvals, 'key': 'approve-dependency-recipe'}
    values.update(changes)
    return service.approve_dag(owner, workflow.id, {writer.id: reviewer.id for writer, reviewer in zip(writers, reviewers)},
        integrator.id, **values)


def test_preapproved_dag_runs_real_sequential_writers_and_join_without_owner_commands(dependency_recipe_ready, monkeypatch):
    prepared = dependency_recipe_ready
    store, owner, host, workflow, _, writers, reviewers, integrator, dispatcher, source, _ = prepared
    before = store.budget(owner, workflow.id)
    revisions = {task.id: task.revision for task in store.tasks(owner, workflow.id)}
    recipe = approve_dependency_recipe(prepared, expected_revisions=revisions)
    assert recipe.version == 3 and approve_dependency_recipe(prepared, expected_revisions=revisions) == recipe
    assert Pipelines(store, dispatcher.root).get(owner, recipe.id) == recipe
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    initial = {task.id: task for task in store.tasks(owner, workflow.id)}
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('DAG used a manual claim'))
    monkeypatch.setattr(store, 'command', lambda *a, **kw: pytest.fail('DAG required an owner command'))
    try:
        for number in range(24):
            dispatcher.tick(host, key=f'dag-tick-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None and handle.process_stopped.is_set()
            if store.workflow(owner, workflow.id).state == 'succeeded':
                break
            assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert store.workflow(owner, workflow.id).state == 'succeeded'
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 7 and {item.task_id for item in attempts} == {
            item.id for item in (*writers, *reviewers, integrator)}
        artifacts = Artifacts(store, dispatcher.root)
        proofs = [Completions(store, dispatcher.root).get(owner, store.task(owner, writer.id).completion_id) for writer in writers]
        for number, (writer, proof) in enumerate(zip(writers, proofs)):
            artifact = artifacts.get(owner, proof.artifact_id)
            workspace = artifacts.workspaces.get(owner, artifact.attempt_id)
            baseline = artifacts.workspaces.root / workspace.id / 'baseline/src/parser.py'
            assert baseline.read_text() == f'value = {number}\n'
            assert set(proof.prerequisites) == set(writer.dependencies)
            assert all(proof.prerequisites[item.task_id].id == item.id for item in proofs[:number] if item.task_id in writer.dependencies)
            assert Completions(store, dispatcher.root).current(owner, proof.id)
            assert store.task(owner, writer.id).semantic_revision == initial[writer.id].semantic_revision + bool(writer.dependencies)
        rows = store.db.execute('SELECT id FROM integrations').fetchall()
        assert len(rows) == 1 and Integration(store, dispatcher.root).current(owner, rows[0]['id'])
        assert (source / 'src/parser.py').read_text() == 'value = 3\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        assert store.budget(owner, workflow.id).reserved == BudgetAmount()
        assert store.budget(owner, workflow.id).spent.model_calls == 3
        assert store.budget(owner, workflow.id).spent.tool_calls <= 120
        assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts').fetchone()[0] == 9
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('change', ['stale-revision', 'reader', 'runtime', 'foreign', 'review-dependency', 'cycle', 'missing-binding', 'source', 'oracle'])
def test_dependency_recipe_rejects_invalid_authority_without_enqueue_or_spend(dependency_recipe_ready, monkeypatch, change):
    prepared = dependency_recipe_ready
    store, owner, host, workflow, _, writers, reviewers, integrator, dispatcher, source, _ = prepared
    values = {}
    if change in {'reader', 'runtime', 'foreign'}:
        owner = owner.model_copy(update={'project_ids': frozenset({'q'})} if change == 'foreign' else {'role': change})
    elif change == 'stale-revision':
        values['expected_revisions'] = {task.id: task.revision + 1 for task in store.tasks(owner, workflow.id)}
    elif change in {'review-dependency', 'cycle'}:
        task = store.task(owner, writers[0].id)
        dependency = reviewers[0].id if change == 'review-dependency' else writers[-1].id
        store.db.execute('UPDATE tasks SET data=? WHERE id=?', (
            task.model_copy(update={'dependencies': (dependency,)}).model_dump_json(), task.id))
    elif change == 'missing-binding':
        store.db.execute('DELETE FROM worker_bindings WHERE task_id=?', (writers[1].id,))
    elif change == 'source':
        (source / 'dirty.bin').write_bytes(b'new owner bytes')
    else:
        registry = VerificationEngine(dispatcher.root / 'verification').oracles
        approval = prepared[-1][writers[-1].id]
        record = registry.read(approval)
        registry.approve(writers[-1].acceptance, [item.model_copy(update={'timeout': 29}) for item in record.checks],
            source.parent / 'dag-oracle-3')
    def call():
        if change in {'reader', 'runtime', 'foreign'}:
            pairs = {writer.id: reviewer.id for writer, reviewer in zip(writers, reviewers)}
            return Pipelines(store, dispatcher.root).approve_dag(owner, workflow.id, pairs, integrator.id,
                source=source, expected_revisions={}, approval_hashes={}, key='invalid-dag-scope')
        return approve_dependency_recipe(prepared, **values)
    if change not in {'source', 'oracle'}:
        monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Invalid authority inspected source'))
    before = store.budget(host, workflow.id), len(store.events(host, workflow.id))
    with pytest.raises(ValueError if change == 'oracle' else StoreError):
        call()
    assert not store.db.execute('SELECT 1 FROM pipelines').fetchone()
    assert store.attempts(host, workflow.id) == []
    assert (store.budget(host, workflow.id), len(store.events(host, workflow.id))) == before


def test_failed_predecessor_leaves_dependent_blocked_while_independent_writer_finishes(dependency_recipe_ready, model_server):
    prepared = dependency_recipe_ready
    store, owner, host, workflow, _, writers, _, _, dispatcher, source, _ = prepared
    independent = store.task(owner, writers[-1].id)
    independent = store.command(owner, independent.id, TaskCommand(operation='revise', expected_revision=independent.revision,
        idempotency_key='make-independent-dag-branch', changes={'dependencies': ()}))
    previous = dispatcher.binding(owner, independent.id)
    dispatcher.bind(owner, independent.id, source, expected_revision=independent.revision, expected_binding_id=previous.id,
        key='bind-independent-dag-branch', brain='heuristic_fsm')
    response = model_server.respond
    def reject_first(request):
        result = response(request)
        if 'Set checked chain value to 1.' in str(request['messages']):
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
                'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'}}]}))
        if 'Set checked chain value to 3.' in str(request['messages']):
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
                'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 3'}}]}))
        return result
    model_server.respond = reject_first
    approve_dependency_recipe(prepared)
    try:
        for number in range(16):
            dispatcher.tick(host, key=f'failed-dag-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None
        assert store.task(owner, independent.id).completion_id is not None
        assert store.task(owner, writers[0].id).completion_id is None
        assert not any(item.task_id == writers[1].id for item in store.attempts(owner, workflow.id))
        assert store.workflow(owner, workflow.id).state != 'succeeded'
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.fixture
def checked_dependency_stage(dependency_recipe_ready):
    prepared = dependency_recipe_ready
    store, owner, host, _, _, writers, reviewers, _, dispatcher, _, _ = prepared
    recipe = approve_dependency_recipe(prepared)
    first = dispatcher.tick(host, key='dag-first-author')
    assert first.attempt is not None and first.attempt.task_id == writers[0].id
    try:
        handle = dispatcher.handles[first.attempt.id]
        assert handle.finished.wait(25), handle.error
        assert handle.error is None
        checked = dispatcher.tick(host, key='dag-first-review')
        assert checked.attempt is not None and checked.attempt.task_id == reviewers[0].id
        assert store.task(owner, reviewers[0].id).state == 'succeeded'
        assert store.task(owner, writers[0].id).state == 'awaiting_review'
        yield prepared, recipe
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('stage', ['completion', 'writer'])
def test_two_connections_materialize_each_dependency_stage_once(checked_dependency_stage, monkeypatch, stage):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    prepared, recipe = checked_dependency_stage
    store, owner, host, workflow, _, writers, _, _, dispatcher, source, _ = prepared
    service = Pipelines(store, dispatcher.root)
    if stage == 'writer':
        assert service.advance(host, workflow.id) == ()
    barrier = Barrier(2)
    if stage == 'completion':
        complete = Completions.complete
        def synchronize(completions, *args, **kwargs):
            barrier.wait(timeout=10)
            return complete(completions, *args, **kwargs)
        monkeypatch.setattr(Completions, 'complete', synchronize)
    else:
        from flycode.orchestration import pipeline

        capture = pipeline.capture
        def synchronize(path, *args, **kwargs):
            result = capture(path, *args, **kwargs)
            if path == source:
                barrier.wait(timeout=10)
            return result
        monkeypatch.setattr(pipeline, 'capture', synchronize)
    before = store.budget(owner, workflow.id)
    def advance(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Pipelines(connection, dispatcher.root).advance(host, workflow.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(advance, range(2))) == [(), ()]
    key = ('completion:' + writers[0].id) if stage == 'completion' else ('writer:' + writers[1].id)
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, key)).fetchone()[0] == 1
    assert store.db.execute('SELECT COUNT(*) FROM task_completions').fetchone()[0] == 1
    assert store.budget(owner, workflow.id) == before
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


@pytest.mark.parametrize('stage', ['completion', 'writer'])
def test_dependency_stage_outbox_failure_recovers_without_duplicate_completion_or_spend(checked_dependency_stage, monkeypatch, stage):
    prepared, recipe = checked_dependency_stage
    store, owner, host, workflow, _, writers, _, _, dispatcher, _, _ = prepared
    service = Pipelines(store, dispatcher.root)
    if stage == 'writer':
        assert service.advance(host, workflow.id) == ()
    key = ('completion:' + writers[0].id) if stage == 'completion' else ('writer:' + writers[1].id)
    before = store.budget(owner, workflow.id)
    event = store._event
    def fail(scope, workflow, kind, **kwargs):
        if (stage == 'completion' and kind == 'pipeline_task_completed') or (stage == 'writer' and kind == 'pipeline_materialized'):
            raise RuntimeError('outbox unavailable')
        return event(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        service.advance(host, workflow.id)
    assert store.task(owner, writers[0].id).state == 'succeeded'
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, key)).fetchone()
    monkeypatch.setattr(store, '_event', event)
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        assert Pipelines(connection, dispatcher.root).advance(host, workflow.id) == ()
        assert Pipelines(connection, dispatcher.root).get(owner, recipe.id) == recipe
    finally:
        connection.close()
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, key)).fetchone()[0] == 1
    assert store.db.execute('SELECT COUNT(*) FROM task_completions').fetchone()[0] == 1
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('change', ['pause', 'source', 'criteria', 'evidence'])
def test_changed_prerequisite_after_preflight_cannot_launch_dependent_worker(checked_dependency_stage, monkeypatch, change):
    from flycode.orchestration import pipeline

    prepared, _ = checked_dependency_stage
    store, owner, host, workflow, _, writers, _, _, dispatcher, source, _ = prepared
    service = Pipelines(store, dispatcher.root)
    assert service.advance(host, workflow.id) == ()
    proof = Completions(store, dispatcher.root).get(owner, store.task(owner, writers[0].id).completion_id)
    capture = pipeline.capture
    changed = []
    def mutate_after_capture(path, *args, **kwargs):
        result = capture(path, *args, **kwargs)
        if path == source and not changed:
            changed.append(True)
            if change in {'pause', 'criteria'}:
                task = store.task(owner, writers[1].id)
                store.command(owner, task.id, TaskCommand(operation='pause' if change == 'pause' else 'revise',
                    expected_revision=task.revision, idempotency_key='revoke-dag-after-preflight',
                    changes={} if change == 'pause' else {'objective': 'A different owner request.'}))
            elif change == 'source':
                (source / 'dirty.bin').write_bytes(b'new owner bytes')
            else:
                review = service.reviews.artifacts.store.db.execute('SELECT data FROM reviews WHERE id=?', (proof.review_id,)).fetchone()
                (dispatcher.root / 'verification' / json.loads(review['data'])['verdict']['evidence_id'] / 'manifest.json').unlink()
        return result
    monkeypatch.setattr(pipeline, 'capture', mutate_after_capture)
    service.advance(host, workflow.id)
    assert changed
    result = dispatcher.tick(host, key='do-not-launch-revoked-dag-input')
    assert result.attempt is None
    assert not any(item.task_id == writers[1].id for item in store.attempts(owner, workflow.id))
    assert not store.db.execute('SELECT 1 FROM workers WHERE attempt_id IN (SELECT id FROM attempts WHERE task_id=?)',
        (writers[1].id,)).fetchone()
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def copy_dependency_graph(prepared, *, registered):
    from flycode.orchestration.projects import Projects

    store, owner, host, original, root, writers, reviewers, integrator, dispatcher, source, approvals = prepared
    project_id = Projects(store).register(source, actor_id=owner.actor_id).id if registered else original.project_id
    owner = owner.model_copy(update={'project_ids': owner.project_ids | {project_id}})
    host = host.model_copy(update={'project_ids': host.project_ids | {project_id}})
    profiles = {identity: identity for task in (*writers, *reviewers, integrator) for identity in task.allowed_profile_ids}
    if registered:
        for identity in profiles:
            old = store._profile(identity)
            profile = store.register_profile(owner, AgentProfileDraft.model_validate({
                **old.model_dump(include=set(AgentProfileDraft.model_fields)), 'project_ids': (project_id,)}))
            store.register_instance(host, profile.id)
            profiles[identity] = profile.id
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=project_id,
        task=TaskDraft.model_validate(root.model_dump(include=set(TaskDraft.model_fields))),
        budget=WorkflowBudget() if registered else WorkflowBudget(max_tool_calls=6)), key='copy-dag-workflow')
    new_root = store.task(owner, workflow.root_task_id)
    mapping, new_writers, new_reviewers = {root.id: new_root.id}, [], []
    def draft(task, **changes):
        return TaskDraft.model_validate({**task.model_dump(include=set(TaskDraft.model_fields)),
            'parent_id': new_root.id, 'dependencies': tuple(mapping[item] for item in task.dependencies),
            'allowed_profile_ids': tuple(profiles[item] for item in task.allowed_profile_ids), **changes})
    for writer, reviewer in zip(writers, reviewers):
        item = store.create_task(owner, workflow.id, draft(writer), key='copy-' + writer.id)
        mapping[writer.id] = item.id
        new_writers.append(item)
        item = store.create_task(owner, workflow.id, draft(reviewer), key='copy-' + reviewer.id)
        mapping[reviewer.id] = item.id
        new_reviewers.append(item)
    new_integrator = store.create_task(owner, workflow.id, draft(integrator,
        budget=integrator.budget if registered else BudgetAmount(tool_calls=6)), key='copy-' + integrator.id)
    mapping[integrator.id] = new_integrator.id
    for writer in new_writers:
        dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision,
            key='copy-dag-bind-' + writer.id, brain='heuristic_fsm')
    return store, owner, host, workflow, new_root, new_writers, new_reviewers, new_integrator, dispatcher, source, {
        mapping[identity]: approval for identity, approval in approvals.items()}


def test_registered_project_runs_the_same_verified_dependency_route(dependency_recipe_ready, monkeypatch):
    prepared = copy_dependency_graph(dependency_recipe_ready, registered=True)
    test_preapproved_dag_runs_real_sequential_writers_and_join_without_owner_commands(prepared, monkeypatch)


def test_cumulative_budget_exhaustion_blocks_further_dependencies_without_applying_owner_source(dependency_recipe_ready):
    prepared = copy_dependency_graph(dependency_recipe_ready, registered=False)
    store, owner, host, workflow, _, writers, _, _, dispatcher, source, _ = prepared
    approve_dependency_recipe(prepared)
    reasons = set()
    try:
        for number in range(16):
            result = dispatcher.tick(host, key=f'bounded-dag-{number}')
            reasons.update(result.reasons)
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None
        assert 'budget_exhausted' in reasons
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.tool_calls == 6 and ledger.free.tool_calls == 0
        assert ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert store.task(owner, writers[0].id).completion_id is not None
        assert store.task(owner, writers[1].id).completion_id is None
        assert not any(item.task_id == writers[2].id for item in store.attempts(owner, workflow.id))
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('automatic', [False, True])
def test_paused_completed_predecessor_blocks_all_new_claim_routes(checked_dependency_stage, automatic):
    prepared, _ = checked_dependency_stage
    store, owner, host, workflow, _, writers, _, _, dispatcher, _, _ = prepared
    service = Pipelines(store, dispatcher.root)
    assert service.advance(host, workflow.id) == ()
    assert service.advance(host, workflow.id) == ()
    first = store.task(owner, writers[0].id)
    store.command(owner, first.id, TaskCommand(operation='pause', expected_revision=first.revision,
        idempotency_key='pause-checked-predecessor'))
    task = store.task(owner, writers[1].id)
    instance = next(item for item in store.instances(host) if item.profile_id in task.allowed_profile_ids and item.state == 'idle')
    before = store.budget(owner, workflow.id), len(store.attempts(owner, workflow.id))
    common = {'instance_id': instance.id, 'expected_instance_revision': instance.revision, 'idempotency_key': 'claim-after-predecessor-pause'}
    if automatic:
        result = store.claim_next(host, AutoClaimRequest(**common))
    else:
        result = store.claim(host, ClaimRequest(**common, task_id=task.id, expected_task_revision=task.revision, reserve=task.budget))
    assert result.attempt is None and 'pipeline_branch_paused' in result.reasons
    assert (store.budget(owner, workflow.id), len(store.attempts(owner, workflow.id))) == before


@pytest.mark.parametrize('change', ['root-dependency', 'paused-branch'])
def test_valid_root_dependencies_and_branch_pause_do_not_block_the_initial_writer(dependency_recipe_ready, change):
    prepared = dependency_recipe_ready
    store, owner, host, _, root, writers, _, _, dispatcher, source, _ = prepared
    if change == 'root-dependency':
        store.command(owner, root.id, TaskCommand(operation='revise', expected_revision=root.revision,
            idempotency_key='explicit-aggregate-dependency', changes={'dependencies': (writers[0].id,)}))
    approve_dependency_recipe(prepared)
    if change == 'paused-branch':
        task = store.task(owner, writers[1].id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision,
            idempotency_key='pause-only-dependent-branch'))
    assert store.task(owner, root.id).state == 'ready'
    selected = dispatcher.tick(host, key='independent-initial-progress')
    try:
        assert selected.attempt is not None and selected.attempt.task_id == writers[0].id, selected.reasons
        handle = dispatcher.handles[selected.attempt.id]
        assert handle.finished.wait(25), handle.error
        assert handle.error is None
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        assert store.task(owner, writers[1].id).current_attempt_id is None
    finally:
        for handle in dispatcher.handles.values():
            handle.close()
