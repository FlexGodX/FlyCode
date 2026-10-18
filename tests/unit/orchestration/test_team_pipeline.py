"""An owner-approved team has independent exact reviews and one joint result."""

import json
import shutil

import pytest
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import BudgetAmount, TaskCommand, TaskDraft
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import Store, StoreError
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def team_ready(pipeline_ready, tmp_path, model_server, request):  # noqa: F811
    store, owner, host, workflow, root, writer, reviewer, integrator, dispatcher, source, _ = pipeline_ready
    second = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **writer.model_dump(include=set(TaskDraft.model_fields)),
        'title': 'Repair other implementation', 'objective': 'Repair other implementation without changing tests.'}), key='second-writer')
    second_review = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **reviewer.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Review other implementation'}), key='second-review')
    integrator = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='team-integrator', changes={'dependencies': (reviewer.id, second_review.id), 'budget': {'tool_calls': 40}}))
    (source / 'src/other.py').write_text('value = 0\n')
    comparison = '== 1' if getattr(request, 'param', None) == 'combined-failure' else '>= 1'
    (source / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n'
        f'    assert runpy.run_path("src/parser.py")["value"] + runpy.run_path("src/other.py")["value"] {comparison}\n')
    oracle = tmp_path / 'team-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_bytes((source / 'tests/test_parser.py').read_bytes())
    engine = VerificationEngine(dispatcher.root / 'verification')
    old = engine.oracles.read(pipeline_ready[-1])
    approval = engine.oracles.approve(root.acceptance, list(old.checks), oracle.parent)
    for task in (writer, second):
        previous = dispatcher.binding(owner, task.id) if task.id == writer.id else None
        dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='team-bind-' + task.id,
            expected_binding_id=previous.id if previous else None, brain='heuristic_fsm')
    bad_second = getattr(request, 'param', None) == 'bad-second'
    overlap = getattr(request, 'param', None) == 'overlap'
    def respond(request):
        second = 'Repair other implementation' in str(request['messages'])
        path = 'src/other.py' if second and not overlap else 'src/parser.py'
        value = -1 if bad_second and second else 2 if overlap and second else 1
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file',
            'arguments': {'path': path, 'old_text': 'value = 0', 'new_text': f'value = {value}'}}]}))
    model_server.respond = respond
    return store, owner, host, workflow, root, (writer, second), (reviewer, second_review), integrator, dispatcher, source, approval


def approve_team(prepared, scope=None, **changes):
    store, owner, _, workflow, root, writers, reviewers, integrator, dispatcher, source, approval = prepared
    service = Pipelines(store, dispatcher.root)
    assert hasattr(service, 'approve_team'), 'Owner-approved independent team recipe is missing'
    initial = (root, *writers, *reviewers, integrator)
    values = {'source': source, 'expected_revisions': {task.id: task.revision for task in initial},
        'approval_hashes': {task.id: approval for task in initial}, 'key': 'approve-team'}
    pairs = changes.pop('pairs', {writer.id: reviewer.id for writer, reviewer in zip(writers, reviewers)})
    values.update(changes)
    return service.approve_team(scope or owner, workflow.id,
        pairs, integrator.id, **values)


def test_team_approval_is_inert_idempotent_and_pins_every_writer(team_ready):
    store, owner, _, workflow, _, writers, _, _, dispatcher, _, _ = team_ready
    before = store.budget(owner, workflow.id)
    recipe = approve_team(team_ready)
    assert recipe.version == 2 and approve_team(team_ready) == recipe
    assert Pipelines(store, dispatcher.root).get(owner, recipe.id) == recipe
    assert {branch.writer_id for branch in recipe.branches} == {task.id for task in writers}
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    assert not store.db.execute('SELECT 1 FROM model_calls').fetchone()
    assert not store.db.execute('SELECT 1 FROM tool_calls').fetchone()


def test_team_executes_two_real_writer_processes_reviews_and_one_current_integration(team_ready, monkeypatch):
    store, owner, host, workflow, _, writers, reviewers, integrator, dispatcher, source, _ = team_ready
    approve_team(team_ready)
    dirty = (source / 'dirty.bin').read_bytes()
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Team used a manual claim'))
    try:
        for number in range(10):
            dispatcher.tick(host, key=f'team-tick-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None and handle.process_stopped.is_set()
            if store.workflow(owner, workflow.id).state == 'succeeded':
                break
        assert store.workflow(owner, workflow.id).state == 'succeeded'
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 5
        assert {item.task_id for item in attempts} == {task.id for task in (*writers, *reviewers, integrator)}
        assert all(task.state == 'succeeded' for task in store.tasks(owner, workflow.id))
        row = store.db.execute('SELECT id FROM integrations').fetchone()
        assert row is not None and Integration(store, dispatcher.root).current(owner, row['id'])
        assert store.db.execute('SELECT count(*) FROM pipeline_receipts').fetchone()[0] == 3
        assert store.budget(owner, workflow.id).reserved == BudgetAmount()
        assert not store.budget(owner, workflow.id).uncertain
        assert all((source / name).read_text() == 'value = 1\n' for name in ('src/parser.py', 'src/other.py'))
        assert (source / 'dirty.bin').read_bytes() == dirty
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('invalid', ['reader', 'runtime', 'foreign', 'duplicate-role', 'stale-revision', 'missing-binding'])
def test_invalid_team_authority_fails_before_source_reads_or_execution(team_ready, monkeypatch, invalid):
    store, owner, _, workflow, _, writers, reviewers, _, _, _, _ = team_ready
    values, scope = {}, owner
    if invalid in {'reader', 'runtime', 'foreign'}:
        scope = owner.model_copy(update={'project_ids': frozenset({'q'})} if invalid == 'foreign' else {'role': invalid})
    elif invalid == 'duplicate-role':
        values['pairs'] = {writer.id: reviewers[0].id for writer in writers}
    elif invalid == 'stale-revision':
        values['expected_revisions'] = {task.id: task.revision + 1 for task in store.tasks(owner, workflow.id)}
    else:
        store.db.execute('DELETE FROM worker_bindings WHERE task_id=?', (writers[1].id,))
    before = store.budget(owner, workflow.id)
    monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Invalid authority inspected source'))
    with pytest.raises(StoreError, match='scope|revision|binding_required'):
        approve_team(team_ready, scope, **values)
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    assert not store.db.execute('SELECT 1 FROM pipelines').fetchone()


def test_changed_second_binding_blocks_all_writers_after_team_approval(team_ready):
    store, owner, host, workflow, _, writers, _, _, dispatcher, _, _ = team_ready
    approve_team(team_ready)
    store.db.execute('DELETE FROM worker_bindings WHERE task_id=?', (writers[1].id,))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='revoked-second-binding')
    assert result.attempt is None and 'binding_revision' in result.reasons
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before


def test_team_initial_outbox_failure_rolls_back_every_enqueue(team_ready, monkeypatch):
    store, owner, _, workflow, _, _, _, _, _, _, _ = team_ready
    before = store.tasks(owner, workflow.id), store.workflow(owner, workflow.id), store.budget(owner, workflow.id)
    original = store._event
    def fail(scope, workflow, kind, **kwargs):
        if kind == 'task_enqueue':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        approve_team(team_ready)
    assert (store.tasks(owner, workflow.id), store.workflow(owner, workflow.id), store.budget(owner, workflow.id)) == before
    assert not store.db.execute('SELECT 1 FROM pipelines').fetchone()


def test_team_recipe_reloads_and_identical_replay_keeps_original_authority(team_ready):
    store, owner, _, workflow, _, _, _, _, dispatcher, _, _ = team_ready
    recipe = approve_team(team_ready)
    other = Store(store.path, catalogue=store.catalogue)
    try:
        assert Pipelines(other, dispatcher.root).get(owner, recipe.id) == recipe
        assert approve_team(team_ready) == recipe
        with pytest.raises(StoreError, match='idempotency'):
            approve_team(team_ready, reruns=1)
        assert not other.attempts(owner, workflow.id)
    finally:
        other.close()


@pytest.fixture
def published_team(team_ready):
    _, _, host, _, _, _, _, _, dispatcher, _, _ = team_ready
    recipe = approve_team(team_ready)
    selected = dispatcher.tick(host, key='team-first-publication')
    assert selected.attempt is not None
    try:
        handle = dispatcher.handles[selected.attempt.id]
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
        yield team_ready, recipe, selected.attempt
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_available_branch_review_does_not_wait_for_the_other_writer_and_is_unique_across_connections(published_team):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    prepared, recipe, author = published_team
    store, owner, host, workflow, _, writers, _, _, dispatcher, _, _ = prepared
    branch = next(item for item in recipe.branches if item.writer_id == author.task_id)
    before = store.budget(owner, workflow.id)
    barrier = Barrier(2)
    def advance(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=5)
            return Pipelines(connection, dispatcher.root).advance(host.model_copy(update={'actor_id': f'team-host-{number}'}), workflow.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(advance, range(2))) == [(), ()]
    row = store.db.execute('SELECT stage FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchall()
    assert [item['stage'] for item in row] == ['review:' + branch.reviewer_id]
    assert store.task(owner, branch.reviewer_id).state == 'ready'
    assert store.task(owner, next(task.id for task in writers if task.id != author.task_id)).state == 'ready'
    assert store.db.execute('SELECT count(*) FROM review_bindings WHERE task_id=?', (branch.reviewer_id,)).fetchone()[0] == 1
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ()
    assert store.budget(owner, workflow.id) == before and len(store.attempts(owner, workflow.id)) == 1


def test_team_review_materialization_outbox_failure_retains_the_artifact_without_partial_inputs(published_team, monkeypatch):
    prepared, recipe, _ = published_team
    store, owner, host, workflow, _, _, reviewers, _, dispatcher, _, _ = prepared
    before = store.tasks(owner, workflow.id), store.budget(owner, workflow.id)
    original = store._event
    def fail(scope, workflow, kind, **kwargs):
        if kind == 'pipeline_materialized':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        Pipelines(store, dispatcher.root).advance(host, workflow.id)
    assert (store.tasks(owner, workflow.id), store.budget(owner, workflow.id)) == before
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()
    assert all(not store.task(owner, task.id).input_artifacts for task in reviewers)
    assert store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 1


def test_pause_after_team_artifact_preflight_blocks_all_materialization(published_team, monkeypatch):
    from flycode.orchestration.artifacts import Artifacts

    prepared, recipe, _ = published_team
    store, owner, host, workflow, _, _, reviewers, _, dispatcher, _, _ = prepared
    original = Artifacts.get
    def pause_after_read(service, *args, **kwargs):
        artifact = original(service, *args, **kwargs)
        target = store.task(owner, reviewers[1].id)
        store.command(owner, target.id, TaskCommand(operation='pause', expected_revision=target.revision, idempotency_key='pause-team-review'))
        return artifact
    monkeypatch.setattr(Artifacts, 'get', pause_after_read)
    before = store.budget(owner, workflow.id)
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ('pipeline_changed',)
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('team_ready', ['combined-failure', 'bad-second', 'overlap'], indirect=True)
def test_failed_branch_or_combined_regression_cannot_apply_or_complete_team(team_ready):
    store, owner, host, workflow, _, _, _, _, dispatcher, source, _ = team_ready
    approve_team(team_ready)
    dirty = (source / 'dirty.bin').read_bytes()
    try:
        for number in range(8):
            dispatcher.tick(host, key=f'negative-team-tick-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None and handle.process_stopped.is_set()
        assert store.workflow(owner, workflow.id).state != 'succeeded'
        assert all((source / path).read_text() == 'value = 0\n' for path in ('src/parser.py', 'src/other.py'))
        assert (source / 'dirty.bin').read_bytes() == dirty
        assert store.db.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 2
        assert store.db.execute('SELECT count(*) FROM reviews').fetchone()[0] == 2
        assert not store.db.execute("SELECT 1 FROM integrations WHERE json_extract(data,'$.state')='applied'").fetchone()
        for row in store.db.execute('SELECT id FROM integrations'):
            assert not Integration(store, dispatcher.root).current(owner, row['id'])
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('invalid', ['writer-dependency', 'missing-reviewer-dependency', 'split-source'])
def test_team_rejects_unsupported_dependency_semantics_or_different_sources_before_capture(team_ready, monkeypatch, tmp_path, invalid):
    store, owner, _, workflow, _, writers, reviewers, integrator, dispatcher, source, _ = team_ready
    if invalid == 'split-source':
        other = tmp_path / 'other-source'
        shutil.copytree(source, other)
        previous = dispatcher.binding(owner, writers[1].id)
        dispatcher.bind(owner, writers[1].id, other, expected_revision=writers[1].revision,
            expected_binding_id=previous.id, key='separate-source', brain='heuristic_fsm')
    else:
        task = writers[0] if invalid == 'writer-dependency' else integrator
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='unsupported-dependency', changes={'dependencies': (reviewers[0].id,)}))
    revisions = {task.id: task.revision for task in store.tasks(owner, workflow.id)}
    monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Invalid graph inspected source'))
    with pytest.raises(StoreError, match='pipeline_scope'):
        approve_team(team_ready, expected_revisions=revisions)
    assert store.attempts(owner, workflow.id) == []


def test_changed_source_after_one_branch_publication_blocks_review_and_keeps_owner_bytes(published_team):
    prepared, recipe, _ = published_team
    store, owner, host, workflow, _, _, _, _, dispatcher, source, _ = prepared
    before = store.budget(owner, workflow.id)
    (source / 'dirty.bin').write_bytes(b'owner edit after publication')
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ('snapshot_changed',)
    assert not store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()
    assert store.budget(owner, workflow.id) == before
    assert (source / 'dirty.bin').read_bytes() == b'owner edit after publication'
