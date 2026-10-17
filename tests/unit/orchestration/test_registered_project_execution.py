"""Registered physical project authority survives byte-identical folder substitution."""
import shutil

import pytest
from test_canonical_project_registry import queued

from flycode.domain.orchestration import BudgetAmount
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.projects import Projects
from flycode.orchestration.runtime import Runtime
from flycode.orchestration.store import Store, StoreError


def replace(source, *, ancestor=False):
    if ancestor:
        displaced = source.parent.with_name(source.parent.name + '-displaced')
        source.parent.rename(displaced)
        source.parent.mkdir()
        (displaced / source.name).rename(source)
    else:
        replacement = source.with_name(source.name + '-replacement')
        shutil.copytree(source, replacement)
        source.rename(source.with_name(source.name + '-displaced'))
        replacement.rename(source)


@pytest.fixture
def registered(tmp_path):
    source = tmp_path / 'approved/root'
    (source / 'src').mkdir(parents=True)
    (source / 'src/example.py').write_text('answer = 0\n')
    (source / 'user.bin').write_bytes(b'\x00\xff user bytes\r\n')
    store = Store(tmp_path / 'queue.sqlite')
    project = Projects(store).register(source, actor_id='owner')
    owner, host, workflow, task, request = queued(store, project.id)
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind', brain='heuristic_fsm')
    try:
        yield store, owner, host, workflow, task, request, source, dispatcher, project
    finally:
        for handle in dispatcher.handles.values():
            handle.close()
            assert handle.finished.wait(10), handle.error
        store.close()


@pytest.mark.parametrize('ancestor', [False, True], ids=['root', 'ancestor'])
def test_registered_root_substitution_blocks_dispatch_before_assignment(registered, ancestor):
    store, owner, host, workflow, _, _, source, dispatcher, _ = registered
    replace(source, ancestor=ancestor)
    result = dispatcher.tick(host, key='replaced')
    assert result.attempt is None and 'project_changed' in result.reasons
    assert not store.attempts(owner, workflow.id)
    assert store.budget(owner, workflow.id).spent == store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert (source / 'user.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_root_replacement_between_claim_and_launch_starts_no_worker(registered, monkeypatch):
    store, owner, host, workflow, _, _, source, dispatcher, _ = registered
    original = dispatcher._launch

    def after_claim(scope, attempt_id):
        replace(source)
        return original(scope, attempt_id)

    monkeypatch.setattr(dispatcher, '_launch', after_claim)
    result = dispatcher.tick(host, key='replace-after-claim')
    assert result.attempt is not None
    assert not store.db.execute('SELECT 1 FROM workers').fetchone()
    assert not store.db.execute('SELECT 1 FROM workspaces').fetchone()
    stopped = store._attempt(result.attempt.id)
    assert stopped.stop_confirmed and stopped.state == 'failed'
    assert store.budget(owner, workflow.id).spent == store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert any(event.kind == 'worker_dispatch_launch_blocked' and event.data['reason'] == 'project_changed'
        for event in store.events(owner, workflow.id))


@pytest.mark.parametrize('ancestor', [False, True], ids=['root', 'ancestor'])
def test_direct_runtime_rejects_changed_registered_root_without_effects(registered, ancestor):
    store, owner, host, workflow, _, request, source, dispatcher, _ = registered
    attempt = store.claim(host, request).attempt
    replace(source, ancestor=ancestor)
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='project_changed'):
        Runtime(store, dispatcher.root).launch(host, attempt.id, attempt.fence, source)
    assert not store.db.execute('SELECT 1 FROM workers').fetchone()
    assert not store.db.execute('SELECT 1 FROM workspaces').fetchone()
    assert store.budget(owner, workflow.id) == before


def test_registered_task_cannot_launch_another_registered_folder(registered, tmp_path):
    store, owner, host, workflow, _, request, _, dispatcher, _ = registered
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'user.bin').write_bytes(b'other bytes')
    Projects(store).register(other, actor_id='owner')
    attempt = store.claim(host, request).attempt
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='project_scope'):
        Runtime(store, dispatcher.root).launch(host, attempt.id, attempt.fence, other)
    assert not store.db.execute('SELECT 1 FROM workers').fetchone()
    assert store.budget(owner, workflow.id) == before
    assert (other / 'user.bin').read_bytes() == b'other bytes'


@pytest.fixture
def registered_delivery(registered, model_server, tmp_path):
    from test_result_delivery import checks, repair, reviewer

    from flycode.domain.orchestration import (
        AgentProfileDraft,
        ClaimRequest,
        ExecutionPermission,
        TaskCommand,
    )
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, _, source, _, _ = registered
    model_server.respond = lambda request: model_server.completion(
        '{"candidates":[{"tool":"read_file","arguments":{"path":"src/example.py"}}]}')
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store.catalogue = catalogue
    permission = ExecutionPermission(approved=True, read_scopes=('.',), write_scopes=('src',),
        tools=('read_file', 'edit_file', 'run_tests'))
    contract = AcceptanceContract(id='repair', version=1, criteria=[AcceptanceCriterion(id='result',
        check_ids=['regression'], expected_case_ids=['tests/test_example.py::test_example'])])
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='prepare-repair', changes={'permissions': permission.model_dump(),
            'acceptance': contract.model_dump(), 'budget': {'model_calls': 2, 'tool_calls': 3}}))
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Repair only the approved source file.', project_ids=(task.project_id,),
        permissions=permission, context_limit=8192))
    assert profile.available
    instance = store.register_instance(host, profile.id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key='repair-claim'))
    assert claim.attempt is not None, claim.reasons
    attempt = claim.attempt
    (source / 'tests').mkdir()
    owned = store, owner, host, workflow, task, attempt, source
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    return owned, (runtime, artifact, review, approval)


def test_registered_integration_rejects_substituted_source_before_baseline(registered_delivery):
    from test_result_delivery import reviewer

    from flycode.orchestration.integration import Integration

    owned, prepared = registered_delivery
    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = prepared
    integrator = reviewer(owned, [artifact])
    before = store.budget(owner, workflow.id)
    replace(source)
    with pytest.raises(StoreError, match='project_changed'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
    assert not store.db.execute('SELECT 1 FROM source_claims').fetchone()
    assert store.budget(owner, workflow.id) == before
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_registered_integration_rechecks_after_claim_before_baseline(registered_delivery, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration import integration as module
    from flycode.orchestration.sources import Sources

    owned, prepared = registered_delivery
    store, owner, host, _workflow, task, _, source = owned
    runtime, artifact, review, approval = prepared
    integrator = reviewer(owned, [artifact])
    original_acquire, original_capture = Sources.acquire, module.capture
    claimed, baseline_read = [], []

    def replace_after_acquire(sources, *args, **kwargs):
        claim = original_acquire(sources, *args, **kwargs)
        claimed.append(claim.id)
        replace(source)
        return claim

    def observe_capture(root, *args, **kwargs):
        if claimed and root == source and args:
            baseline_read.append(root)
        return original_capture(root, *args, **kwargs)

    monkeypatch.setattr(Sources, 'acquire', replace_after_acquire)
    monkeypatch.setattr(module, 'capture', observe_capture)
    service = module.Integration(store, runtime.root)
    with pytest.raises(StoreError, match='project_changed'):
        service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (integrator.id,)).fetchone()
    result = service.get(owner, row['id'])
    assert not baseline_read, 'Unregistered replacement was captured as an admitted source baseline'
    assert claimed and result.state == 'reconciling'
    assert Sources(store).get(claimed[0]).state == 'held'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source.with_name(source.name + '-displaced') / 'src/example.py').read_text() == 'answer = 0\n'


def test_runtime_rechecks_physical_authority_after_workspace_capture(registered, monkeypatch):
    store, owner, host, workflow, _, request, source, dispatcher, _ = registered
    attempt = store.claim(host, request).attempt
    runtime = Runtime(store, dispatcher.root)
    prepare = runtime.workspaces.prepare

    def replace_after_capture(*args, **kwargs):
        workspace = prepare(*args, **kwargs)
        replace(source)
        return workspace

    monkeypatch.setattr(runtime.workspaces, 'prepare', replace_after_capture)
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='project_changed'):
        runtime.launch(host, attempt.id, attempt.fence, source)
    assert store.db.execute('SELECT 1 FROM workspaces').fetchone()
    assert not store.db.execute('SELECT 1 FROM workers').fetchone()
    assert not store.db.execute('SELECT 1 FROM execution_lanes').fetchone()
    assert store.budget(owner, workflow.id) == before
    assert not store._attempt(attempt.id).stop_confirmed


def test_stale_revoked_registration_cannot_create_new_binding_or_dispatch(registered):
    store, owner, host, workflow, task, _, source, dispatcher, project = registered
    binding = dispatcher.binding(owner, task.id)
    Projects(store).revoke(owner, project.id, expected_revision=project.revision)
    with pytest.raises(StoreError, match='project_revoked'):
        dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='stale-scope',
            expected_binding_id=binding.id)
    result = dispatcher.tick(host, key='stale-project')
    assert result.attempt is None
    assert not store.attempts(owner, workflow.id)
    assert store.budget(owner, workflow.id).spent == store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert dispatcher.binding(owner, task.id) == binding


@pytest.mark.parametrize('identity', ['historical-project', 'a' * 32])
def test_unregistered_historical_ids_keep_low_level_execution_compatibility(tmp_path, identity):
    source = tmp_path / 'legacy'
    source.mkdir()
    (source / 'example.py').write_text('answer = 0\n')
    store = Store(tmp_path / 'legacy.sqlite')
    owner, host, _workflow, task, _ = queued(store, identity)
    dispatcher = Dispatcher(store, tmp_path / 'legacy-runtime')
    dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='historical', brain='heuristic_fsm')
    try:
        result = dispatcher.tick(host, key='legacy-dispatch')
        assert result.attempt is not None
        handle = dispatcher.handles[result.attempt.id]
        assert handle.finished.wait(15), handle.error
        assert handle.process_stopped.is_set()
        assert not store.db.execute('SELECT 1 FROM canonical_projects').fetchone()
        assert store.task(owner, task.id).project_id == identity
        assert (source / 'example.py').read_text() == 'answer = 0\n'
    finally:
        for handle in dispatcher.handles.values():
            handle.close()
            assert handle.finished.wait(10), handle.error
        store.close()


def test_registered_current_project_integrates_with_fs_checks_outside_sql(registered_delivery, monkeypatch):
    from test_result_delivery import reviewer

    from flycode.orchestration.integration import Integration

    owned, prepared = registered_delivery
    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = prepared
    original = Projects.resolve
    inspected = []

    def outside_sql(projects, root):
        assert not projects.store.db.in_transaction
        inspected.append(root)
        return original(projects, root)

    monkeypatch.setattr(Projects, 'resolve', outside_sql)
    integrator = reviewer(owned, [artifact])
    service = Integration(store, runtime.root)
    result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert result.state == 'completed' and service.current(owner, result.id)
    assert inspected and set(inspected) == {source}
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'user.bin').read_bytes() == b'\x00\xff user bytes\r\n'
