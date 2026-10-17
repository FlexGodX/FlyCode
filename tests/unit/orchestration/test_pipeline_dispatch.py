"""Owner recipes materialize exact sibling stages without granting owner authority."""
import importlib.util
import json
import time

import pytest
from test_store import draft, setup  # noqa: F401

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ExecutionPermission,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.domain.verification import CheckSpec
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.integration import Integration
from flycode.orchestration.store import StoreError
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def pipeline_ready(setup, tmp_path, model_server):  # noqa: F811
    store, owner, host, original, *_ = setup
    store.clock = time.time
    developer = store.register_profile(owner, AgentProfileDraft.model_validate({
        **original.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 8192}))
    store.register_instance(host, developer.id)
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    checker = store.register_profile(owner, AgentProfileDraft(role='reviewer', model_ref=developer.model_ref,
        instructions='Independently verify immutable output.', project_ids=('p',), permissions=permission, context_limit=8192))
    store.register_instance(host, checker.id)
    root_draft = draft(developer.permissions, budget=BudgetAmount(model_calls=2, tool_calls=3))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='p', task=root_draft, budget=WorkflowBudget()), key='pipeline-root')
    root = store.task(owner, workflow.root_task_id)
    writer = store.create_task(owner, workflow.id, root_draft.model_copy(update={
        'parent_id': root.id, 'allowed_profile_ids': (developer.id,)}), key='pipeline-writer')
    reviewer = store.create_task(owner, workflow.id, TaskDraft(title='Review output', kind='review', objective='Verify exact writer output.',
        parent_id=root.id, acceptance=root.acceptance, permissions=permission, budget=BudgetAmount(tool_calls=4),
        allowed_profile_ids=(checker.id,)), key='pipeline-reviewer')
    integrator = store.create_task(owner, workflow.id, TaskDraft(title='Integrate output', kind='review', objective='Verify and apply the reviewed output.',
        parent_id=root.id, dependencies=(reviewer.id,), acceptance=root.acceptance, permissions=permission,
        budget=BudgetAmount(tool_calls=25), allowed_profile_ids=(checker.id,)), key='pipeline-integrator')
    source = tmp_path / 'source'
    (source / 'src').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'src/parser.py').write_text('value = 0\n')
    (source / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n    assert runpy.run_path("src/parser.py")["value"] == 1\n')
    (source / 'dirty.bin').write_bytes(b'\x00\xff keep owner bytes\r\n')
    oracle = tmp_path / 'oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_bytes((source / 'tests/test_parser.py').read_bytes())
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    checks = [CheckSpec(id=stage, stage=stage, expected_outcome='failed' if stage == 'baseline' else 'passed',
        targets=['tests/test_parser.py::test_parse'], dependencies=[] if previous is None else [previous])
        for stage, previous in [('baseline', None), ('target', 'baseline'), ('regression', 'target'), ('integration', 'regression')]]
    approval = VerificationEngine(dispatcher.root / 'verification').oracles.approve(root.acceptance, checks, oracle.parent)
    dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision, key='approve-writer', brain='heuristic_fsm')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    return store, owner, host, workflow, root, writer, reviewer, integrator, dispatcher, source, approval


def approve(prepared, scope=None, **changes):
    assert importlib.util.find_spec('flycode.orchestration.pipeline') is not None, 'Future-output recipe service is missing'
    from flycode.orchestration.pipeline import Pipelines
    store, owner, _, workflow, root, writer, reviewer, integrator, dispatcher, source, approval = prepared
    values = {'source': source, 'expected_revisions': {item.id: item.revision for item in (root, writer, reviewer, integrator)},
        'approval_hashes': {item.id: approval for item in (root, writer, reviewer, integrator)}, 'key': 'approve-pipeline'}
    values.update(changes)
    return Pipelines(store, dispatcher.root).approve(scope or owner, workflow.id, writer.id, reviewer.id, integrator.id, **values)


@pytest.mark.parametrize('durable_catalogue', [False, True], ids=['memory-catalogue', 'restored-catalogue'])
def test_recipe_drives_actual_subprocess_dag_with_zero_manual_claims(pipeline_ready, monkeypatch, durable_catalogue):
    store, owner, host, workflow, _root, writer, reviewer, integrator, dispatcher, source, _ = pipeline_ready
    if durable_catalogue:
        from flycode.llm.catalogue import ModelCatalogue

        catalogue = ModelCatalogue(store.catalogue.base_url, database=store.path)
        catalogue.discover()
        catalogue.probe(store._profile(writer.allowed_profile_ids[0]).model_ref, 'primary')
        store.catalogue = ModelCatalogue(catalogue.base_url, database=store.path)
    before = store.budget(owner, workflow.id)
    recipe = approve(pipeline_ready)
    assert approve(pipeline_ready) == recipe
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    generation = store.workflow(owner, workflow.id).semantic_revision
    monkeypatch.setattr(store, 'claim', lambda *a, **kw: pytest.fail('Pipeline used manual claim'))
    try:
        for number in range(5):
            dispatcher.tick(host, key=f'pipeline-tick-{number}')
            for handle in dispatcher.handles.values():
                assert handle.finished.wait(25), handle.error
                assert handle.error is None and handle.process_stopped.is_set()
            if store.workflow(owner, workflow.id).state == 'succeeded':
                break
        assert store.workflow(owner, workflow.id).state == 'succeeded'
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 3 and {item.task_id for item in attempts} == {writer.id, reviewer.id, integrator.id}
        row = store.db.execute('SELECT id FROM integrations').fetchone()
        assert row is not None and Integration(store, dispatcher.root).current(owner, row['id'])
        assert all(item.state == 'succeeded' for item in store.tasks(owner, workflow.id))
        assert store.workflow(owner, workflow.id).semantic_revision == generation
        assert (source / 'src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        assert store.budget(owner, workflow.id).reserved == BudgetAmount()
        assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts').fetchone()[0] == 2
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


@pytest.mark.parametrize('change', ['inputs', 'objective', 'pause', 'oracle', 'workflow_budget'])
def test_changed_recipe_authority_blocks_writer_before_any_assignment(pipeline_ready, change):
    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.store import changed

    store, owner, host, workflow, root, _writer, reviewer, _, dispatcher, source, _ = pipeline_ready
    approve(pipeline_ready)
    if change == 'pause':
        task = store.task(owner, reviewer.id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision, idempotency_key='pause-review'))
    elif change == 'workflow_budget':
        current = store.workflow(owner, workflow.id)
        with store.transaction():
            store._save_workflow(changed(current, budget=changed(current.budget, max_tool_calls=119), revision=current.revision + 1))
    elif change == 'oracle':
        from flycode.orchestration.review_bindings import ReviewBindings
        registry = ReviewBindings(store, dispatcher.root).oracles
        old = registry.read(pipeline_ready[-1])
        checks = [check.model_copy(update={'timeout': 29}) for check in old.checks]
        registry.approve(root.acceptance, checks, source.parent / 'oracle')
    else:
        task = store.task(owner, reviewer.id)
        changes = {'input_artifacts': {'a' * 32: 'b' * 64}} if change == 'inputs' else {'objective': 'A different owner requirement.'}
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='revise-review', changes=changes))
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='changed-pipeline')
    try:
        assert result.attempt is None and result.reasons
        assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_recipe_requires_real_worker_binding_before_source_inspection(pipeline_ready, monkeypatch):
    store, _, _, _, _, writer, *_ = pipeline_ready
    store.db.execute('DELETE FROM worker_bindings WHERE task_id=?', (writer.id,))
    monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Unbound source inspected'))
    with pytest.raises(StoreError, match='binding_required'):
        approve(pipeline_ready)


@pytest.mark.parametrize('role', ['reader', 'runtime', 'foreign'])
def test_recipe_authority_cannot_be_supplied_by_runtime(pipeline_ready, monkeypatch, role):
    from flycode.domain.orchestration import AccessScope
    store, _, _, workflow, *_ = pipeline_ready
    scope = AccessScope(actor_id='untrusted', project_ids={'q'} if role == 'foreign' else {'p'},
        role='owner' if role == 'foreign' else role)
    monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Untrusted source inspected'))
    with pytest.raises(StoreError, match='scope'):
        approve(pipeline_ready, scope)
    assert store.db.execute('SELECT COUNT(*) FROM pipelines').fetchone()[0] == 0
    assert store.workflow(pipeline_ready[1], workflow.id).state == 'draft'


@pytest.fixture
def published_pipeline(pipeline_ready):
    _store, _, host, _, _, _, _, _, dispatcher, *_ = pipeline_ready
    recipe = approve(pipeline_ready)
    result = dispatcher.tick(host, key='publish-before-materialization')
    assert result.attempt is not None
    handle = dispatcher.handles[result.attempt.id]
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
        yield pipeline_ready, recipe
    finally:
        handle.close()


def test_materialization_receipt_survives_restart_and_is_unique_across_actors(published_pipeline):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.pipeline import Pipelines
    from flycode.orchestration.store import Store

    prepared, recipe = published_pipeline
    store, owner, host, workflow, _, _, reviewer, _, dispatcher, *_ = prepared
    before = store.budget(owner, workflow.id)
    barrier = Barrier(2)
    def advance(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=5)
            return Pipelines(connection, dispatcher.root).advance(host.model_copy(update={'actor_id': f'host-{number}'}), workflow.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, range(2)))
    assert results == [(), ()]
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()[0] == 1
    assert store.db.execute('SELECT COUNT(*) FROM review_bindings WHERE task_id=?', (reviewer.id,)).fetchone()[0] == 1
    assert store.task(owner, reviewer.id).state == 'ready'
    assert store.budget(owner, workflow.id) == before
    assert len(store.attempts(owner, workflow.id)) == 1


def test_materialization_outbox_failure_rolls_back_inputs_binding_and_receipt(published_pipeline, monkeypatch):
    from flycode.orchestration.pipeline import Pipelines

    prepared, recipe = published_pipeline
    store, owner, host, workflow, _, _, reviewer, _, dispatcher, *_ = prepared
    before = store.task(owner, reviewer.id), store.budget(owner, workflow.id)
    original = store._event
    def fail(scope, workflow, kind, **kwargs):
        if kind == 'pipeline_materialized':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        Pipelines(store, dispatcher.root).advance(host, workflow.id)
    assert (store.task(owner, reviewer.id), store.budget(owner, workflow.id)) == before
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM review_bindings WHERE task_id=?', (reviewer.id,)).fetchone()[0] == 0


def test_recipe_metadata_change_after_preflight_blocks_atomic_dispatch(pipeline_ready, monkeypatch):
    from flycode.domain.orchestration import TaskCommand

    store, owner, host, workflow, _, _, reviewer, _, dispatcher, *_ = pipeline_ready
    approve(pipeline_ready)
    original = store.dispatch_bound
    def changed_before_claim(*args, **kwargs):
        task = store.task(owner, reviewer.id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision,
            idempotency_key='pause-after-preflight'))
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'dispatch_bound', changed_before_claim)
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='changed-after-preflight')
    try:
        assert result.attempt is None and 'pipeline_changed' in result.reasons
        assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_pause_after_artifact_preflight_cannot_materialize_inputs(published_pipeline, monkeypatch):
    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.pipeline import Pipelines

    prepared, recipe = published_pipeline
    store, owner, host, workflow, _, _, reviewer, _, dispatcher, *_ = prepared
    original = Artifacts.get
    def pause_after_read(service, *args, **kwargs):
        artifact = original(service, *args, **kwargs)
        task = store.task(owner, reviewer.id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision,
            idempotency_key='pause-after-artifact'))
        return artifact
    monkeypatch.setattr(Artifacts, 'get', pause_after_read)
    before = store.budget(owner, workflow.id)
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ('pipeline_changed',)
    assert store.task(owner, reviewer.id).input_artifacts == {}
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM review_bindings WHERE task_id=?', (reviewer.id,)).fetchone()[0] == 0
    assert store.budget(owner, workflow.id) == before


def test_initial_approval_outbox_failure_rolls_back_enqueue_and_authority(pipeline_ready, monkeypatch):
    store, owner, _, workflow, *_ = pipeline_ready
    before = store.workflow(owner, workflow.id), store.tasks(owner, workflow.id), store.budget(owner, workflow.id)
    events = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    original = store._event
    def fail(scope, workflow, kind, **kwargs):
        if kind == 'task_enqueue':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **kwargs)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        approve(pipeline_ready)
    assert (store.workflow(owner, workflow.id), store.tasks(owner, workflow.id), store.budget(owner, workflow.id)) == before
    assert store.db.execute('SELECT COUNT(*) FROM pipelines').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == events


def test_schema14_upgrade_is_atomic_and_preserves_legacy_state(pipeline_ready, monkeypatch):
    import sqlite3

    from flycode.orchestration.store import Store

    store, owner, _, workflow, *_ = pipeline_ready
    before = store.budget(owner, workflow.id), store.tasks(owner, workflow.id)
    store.db.execute('DROP TABLE pipeline_receipts')
    store.db.execute('DROP TABLE pipelines')
    store.db.execute('PRAGMA user_version=13')
    connect = sqlite3.connect
    def interrupted(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '14' else sqlite3.SQLITE_OK)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', interrupted)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        Store(store.path, catalogue=store.catalogue)
    probe = connect(store.path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 13
        assert probe.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('pipelines','pipeline_receipts')").fetchone()[0] == 0
    finally:
        probe.close()
    assert (store.budget(owner, workflow.id), store.tasks(owner, workflow.id)) == before
    monkeypatch.setattr(sqlite3, 'connect', connect)
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        assert connection.db.execute('PRAGMA user_version').fetchone()[0] == 17
        assert (connection.budget(owner, workflow.id), connection.tasks(owner, workflow.id)) == before
    finally:
        connection.close()


def test_owner_metadata_change_preserves_approved_recipe(pipeline_ready):
    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.pipeline import Pipelines

    store, owner, host, workflow, _, _, reviewer, _, dispatcher, *_ = pipeline_ready
    recipe = approve(pipeline_ready)
    task = store.task(owner, reviewer.id)
    revised = store.command(owner, reviewer.id, TaskCommand(operation='set_metadata', expected_revision=task.revision,
        idempotency_key='rename-review', changes={'title': 'Owner renamed this review'}))
    assert revised.semantic_revision == task.semantic_revision
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ()
    assert store.workflow(owner, workflow.id).semantic_revision == recipe.workflow_semantic_revision
    assert store.attempts(owner, workflow.id) == []


def test_stale_owner_revisions_fail_before_source_inspection(pipeline_ready, monkeypatch):
    root = pipeline_ready[4]
    revisions = {item.id: item.revision for item in pipeline_ready[4:8]}
    revisions[root.id] += 1
    monkeypatch.setattr('flycode.orchestration.pipeline.capture', lambda *a, **kw: pytest.fail('Stale source inspected'))
    with pytest.raises(StoreError, match='revision'):
        approve(pipeline_ready, expected_revisions=revisions)


def test_changed_source_after_publication_blocks_stage_and_retains_owner_bytes(published_pipeline):
    from flycode.orchestration.pipeline import Pipelines

    prepared, recipe = published_pipeline
    store, owner, host, workflow, _, _, reviewer, _, dispatcher, source, _ = prepared
    (source / 'dirty.bin').write_bytes(b'owner changed this after publication')
    before = store.budget(owner, workflow.id)
    assert Pipelines(store, dispatcher.root).advance(host, workflow.id) == ('snapshot_changed',)
    assert store.task(owner, reviewer.id).input_artifacts == {}
    assert store.budget(owner, workflow.id) == before
    assert store.db.execute('SELECT COUNT(*) FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchone()[0] == 0
    assert (source / 'dirty.bin').read_bytes() == b'owner changed this after publication'


def test_other_runtime_root_cannot_advance_or_crash_approved_pipeline(pipeline_ready, tmp_path):
    store, owner, host, workflow, *_ = pipeline_ready
    approve(pipeline_ready)
    other_root = tmp_path / 'other-runtime'
    dispatcher = Dispatcher(store, other_root)
    before = store.budget(owner, workflow.id)
    result = dispatcher.tick(host, key='other-runtime-pipeline')
    assert result.attempt is None and 'pipeline_scope' in result.reasons
    assert store.attempts(owner, workflow.id) == [] and store.budget(owner, workflow.id) == before
    assert not other_root.exists()
