"""Physical project authority is independent of local UI selector strings."""
import importlib.util
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain.orchestration import (
    AccessScope,
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
from flycode.llm.catalogue import ModelCatalogue, ModelDescriptor
from flycode.orchestration.sources import Sources
from flycode.orchestration.store import Store, StoreError


def projects(store):
    assert importlib.util.find_spec('flycode.orchestration.projects') is not None, 'Canonical project registry is missing'
    from flycode.orchestration.projects import Projects
    return Projects(store)


def queued(store, project_id, *, key='queue', budget=None):
    # Seed protocol metadata only. No probe, model request or worker is run.
    catalogue = ModelCatalogue('http://127.0.0.1:1/v1')
    catalogue.entries['test-model'] = ModelDescriptor(model_ref='test-model', model_id='test-model',
        display_name='Protocol fixture', endpoint_ref=catalogue.endpoint_ref, status='ready', roles=['primary'],
        capability_source='explicit-local-probe', context_limit=4096)
    store.catalogue = catalogue
    owner = AccessScope(actor_id='owner', project_ids={project_id}, role='owner')
    host = AccessScope(actor_id='host', project_ids={project_id}, role='runtime')
    permission = ExecutionPermission(approved=True, tools=('read_file',), read_scopes=('.',))
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref='test-model',
        instructions='Inspect only approved local files.', project_ids=(project_id,), permissions=permission, context_limit=4096))
    instance = store.register_instance(host, profile.id)
    contract = AcceptanceContract(id='inspect', version=1, criteria=[AcceptanceCriterion(id='read',
        check_ids=['regression'], expected_case_ids=['tests/test_read.py::test_read'])])
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=project_id, budget=budget or WorkflowBudget(),
        task=TaskDraft(title='Inspect local files', objective='Inspect the approved local folder.', acceptance=contract,
            permissions=permission, budget=BudgetAmount(tool_calls=4))), key=key)
    task = store.task(owner, workflow.root_task_id)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-' + task.id))
    store.start_next(owner, key='start-' + task.id)
    request = ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=BudgetAmount(tool_calls=2), idempotency_key='claim-' + task.id)
    return owner, host, store.workflow(owner, workflow.id), task, request


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / 'workspace/foo'
    root.mkdir(parents=True)
    (root / 'dirty.bin').write_bytes(b'\x00\xff owner bytes\r\n')
    store = Store(tmp_path / 'state.sqlite')
    try:
        yield store, root
    finally:
        store.close()


def test_aliases_are_one_canonical_project_and_distinct_workspace_selectors_do_not_collide(registry, tmp_path):
    store, root = registry
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    service = projects(store)
    first = service.register(root, actor_id='server-owner-a')
    assert service.register(root / '.', actor_id='server-owner-a') == first
    assert service.register(alias, actor_id='server-owner-b') == first
    other = tmp_path / 'other-workspace/foo'
    other.mkdir(parents=True)
    second = service.register(other, actor_id='server-owner-b')
    assert first.id != second.id and first.id not in {'.', './foo'}
    owner_a = AccessScope(actor_id='server-owner-a', role='owner', project_ids={first.id})
    owner_b = AccessScope(actor_id='server-owner-b', role='owner', project_ids={second.id})
    workflow = store.create_workflow(owner_a, WorkflowDraft(project_id=first.id, task=TaskDraft(title='Keep a draft')), key='draft')
    assert store.workflows(owner_b) == []
    with pytest.raises(StoreError, match='scope'):
        store.workflow(owner_b, workflow.id)
    with pytest.raises(StoreError, match='scope'):
        store.create_workflow(owner_b, WorkflowDraft(project_id=first.id, task=TaskDraft(title='Keep a draft')), key='draft')
    assert store.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 2
    assert store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0] == 0
    assert (root / 'dirty.bin').read_bytes() == b'\x00\xff owner bytes\r\n'
    reopened = Store(store.path)
    try:
        assert projects(reopened).resolve(alias) == first
    finally:
        reopened.close()


def test_two_connections_register_exact_root_once(registry):
    store, root = registry
    barrier = Barrier(2)
    def register(actor):
        connection = Store(store.path)
        try:
            barrier.wait()
            return projects(connection).register(root, actor_id=actor)
        finally:
            connection.close()
    with ThreadPoolExecutor(2) as pool:
        first, second = pool.map(register, ['host-a', 'host-b'])
    assert first == second
    assert store.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 1


@pytest.mark.parametrize('replacement', ['root', 'ancestor'])
def test_changed_physical_root_or_ancestor_never_rebinds_existing_id(registry, tmp_path, replacement):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    original = tmp_path / 'displaced'
    if replacement == 'root':
        root.rename(original)
        shutil.copytree(original, root)
    else:
        root.parent.rename(original)
        root.parent.mkdir()
        (original / root.name).rename(root)
        assert root.stat().st_ino == project.root.identity[1]
    for operation in (service.resolve, lambda value: service.register(value, actor_id='owner')):
        with pytest.raises(StoreError, match='project_changed'):
            operation(root)
    assert service.get(project.id) == project
    assert store.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 1
    assert (root / 'dirty.bin').read_bytes() == b'\x00\xff owner bytes\r\n'


def test_resolve_never_registers_or_inspects_inside_sql(registry, monkeypatch):
    store, root = registry
    service = projects(store)
    with pytest.raises(StoreError, match='project_not_registered'):
        service.resolve(root)
    assert store.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 0
    prepare = Sources.prepare
    def inspect(sources, path):
        assert not store.db.in_transaction
        return prepare(sources, path)
    monkeypatch.setattr(Sources, 'prepare', inspect)
    project = service.register(root, actor_id='owner')
    assert service.resolve(root) == project


def test_schema16_failure_preserves_schema15_arbitrary_ids_and_history(tmp_path, monkeypatch):
    path = tmp_path / 'state.sqlite'
    store = Store(path)
    owner = AccessScope(actor_id='old-owner', project_ids={'.'}, role='owner')
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='.', task=TaskDraft(title='Historical draft')), key='old-draft')
    before = {name: [tuple(row) for row in store.db.execute('SELECT * FROM ' + name)]
        for name in ('workflows', 'tasks', 'commands', 'events', 'outbox')}
    store.db.execute('DROP TABLE IF EXISTS canonical_projects')
    store.db.execute('PRAGMA user_version=15')
    store.close()
    connect = sqlite3.connect
    def deny(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '16' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', deny)
        with pytest.raises(sqlite3.DatabaseError):
            Store(path)
    connection = connect(path)
    try:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 15
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='canonical_projects'").fetchone()
        assert {name: list(connection.execute('SELECT * FROM ' + name)) for name in before} == before
    finally:
        connection.close()
    reopened = Store(path)
    try:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == 22
        assert reopened.workflow(owner, workflow.id).project_id == '.'
        assert reopened.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 0
    finally:
        reopened.close()


def test_idle_revoke_releases_only_its_workflow_slot_and_blocks_stale_scope_claims(registry, tmp_path):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    owner, host, workflow, task, request = queued(store, project.id)
    before = store.budget(owner, workflow.id)
    second_root = tmp_path / 'other'
    second_root.mkdir()
    second = service.register(second_root, actor_id='owner')
    second_owner, _, other_workflow, _, _ = queued(store, second.id, key='other')
    assert not other_workflow.active_slot
    revoked = service.revoke(owner, project.id, expected_revision=project.revision)
    assert revoked.state == 'revoked' and revoked.revision == project.revision + 1
    assert service.revoke(owner, project.id, expected_revision=project.revision) == revoked
    with pytest.raises(StoreError, match='revision'):
        service.revoke(owner, project.id, expected_revision=100)
    for operation in (service.resolve, lambda value: service.register(value, actor_id='owner')):
        with pytest.raises(StoreError, match='project_revoked'):
            operation(root)
    current = store.workflow(owner, workflow.id)
    assert current.state == 'incomplete' and not current.active_slot
    assert current.revision == workflow.revision + 1
    assert (current.deadline_at, current.started_at) == (workflow.deadline_at, workflow.started_at)
    assert store.task(owner, task.id) == task and store.budget(owner, workflow.id) == before
    assert store.start_next(owner, key='stale-start') is None
    claimed = store.claim(host, request)
    assert claimed.attempt is None and 'project_revoked' in claimed.reasons
    assert not store.attempts(owner, workflow.id) and store.budget(owner, workflow.id) == before
    event = store.events(owner, workflow.id)[-1]
    assert event.kind == 'project_revoked' and event.task_id == task.id
    assert store.start_next(second_owner, key='start-other-after-revoke').id == other_workflow.id
    assert (root / 'dirty.bin').read_bytes() == b'\x00\xff owner bytes\r\n'


@pytest.mark.parametrize('role,foreign', [('reader', False), ('runtime', False), ('owner', True)])
def test_revoke_requires_host_issued_owner_project_scope(registry, role, foreign):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    scope = AccessScope(actor_id='caller', role=role, project_ids={'foreign'} if foreign else {project.id})
    with pytest.raises(StoreError, match='scope'):
        service.revoke(scope, project.id, expected_revision=1)
    assert service.resolve(root) == project


def test_claim_and_revoke_race_have_one_owner_and_no_reservation_leak(registry):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    owner, host, workflow, _, request = queued(store, project.id)
    barrier = Barrier(2)
    def compete(operation):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait()
            try:
                return projects(connection).revoke(owner, project.id, expected_revision=1) if operation == 'revoke' else connection.claim(host, request)
            except StoreError as error:
                return error.code
        finally:
            connection.close()
    with ThreadPoolExecutor(2) as pool:
        revoked, claimed = pool.map(compete, ['revoke', 'claim'])
    budget = store.budget(owner, workflow.id)
    if revoked == 'project_busy':
        assert claimed.attempt is not None and budget.reserved == BudgetAmount(tool_calls=2)
        assert service.get(project.id).state == 'active'
        assert len(store.attempts(owner, workflow.id)) == 1
    else:
        assert revoked.state == 'revoked' and claimed.attempt is None
        assert 'project_revoked' in claimed.reasons and budget.reserved == BudgetAmount()
        assert not store.attempts(owner, workflow.id)
    assert budget.spent == BudgetAmount()
    assert store.db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0] == 0


@pytest.mark.parametrize('uncertain', [False, True])
def test_held_integration_custody_blocks_revoke_after_attempt_stop(registry, tmp_path, uncertain):
    import os

    from flycode.verification.custody import locked

    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    owner, host, workflow, _, request = queued(store, project.id)
    attempt = store.claim(host, request).attempt
    attempt = store.confirm_started(host, attempt.id, attempt.fence)
    sources = Sources(store)
    with locked(tmp_path / 'integrator.lock', create=True) as descriptor:
        info = os.fstat(descriptor)
        claim = sources.acquire(sources.prepare(root), owner_kind='integration', owner_id='held-integrator',
            coordinator_path=tmp_path / 'integrator.lock', coordinator_identity=(info.st_dev, info.st_ino),
            attempt_id=attempt.id, fence=attempt.fence)
    store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    if uncertain:
        claim = sources.mark_uncertain(claim.id, 'fixture-owner-unknown')
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='project_busy'):
        service.revoke(owner, project.id, expected_revision=1)
    assert service.get(project.id) == project and sources.get(claim.id) == claim
    assert store.budget(owner, workflow.id) == before and store._attempt(attempt.id).stop_confirmed


def test_historical_arbitrary_project_ids_keep_low_level_claim_compatibility(registry):
    store, _ = registry
    owner, host, workflow, _, request = queued(store, '.')
    result = store.claim(host, request)
    assert result.attempt is not None
    assert store.workflow(owner, workflow.id).project_id == '.'
    assert store.db.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0] == 0


def test_revoked_project_cannot_reacquire_slot_from_stale_owner_created_draft(registry):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
    service.revoke(owner, project.id, expected_revision=1)
    owner, host, workflow, _, request = queued(store, project.id)
    assert workflow.state == 'draft' and not workflow.active_slot
    assert store.start_next(owner, key='stale-start-new-draft') is None
    result = store.claim(host, request)
    assert result.attempt is None and 'project_revoked' in result.reasons
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()


def test_busy_revoke_preserves_assignment_and_trusted_stop_remains_available(registry):
    store, root = registry
    service = projects(store)
    project = service.register(root, actor_id='owner')
    owner, host, workflow, task, request = queued(store, project.id)
    attempt = store.claim(host, request).attempt
    before = {name: [tuple(row) for row in store.db.execute('SELECT * FROM ' + name)]
        for name in ('canonical_projects', 'workflows', 'tasks', 'attempts', 'reservations', 'events', 'commands')}
    with pytest.raises(StoreError, match='project_busy'):
        service.revoke(owner, project.id, expected_revision=1)
    assert {name: [tuple(row) for row in store.db.execute('SELECT * FROM ' + name)] for name in before} == before
    # No worker/process/tool was admitted: the host can truthfully settle this
    # unstarted assignment, independently of registration authority.
    stopped = store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    before_budget = store.budget(owner, workflow.id)
    revoked = service.revoke(owner, project.id, expected_revision=1)
    assert revoked.state == 'revoked' and stopped.stop_confirmed
    assert store._attempt(attempt.id) == stopped and store.budget(owner, workflow.id) == before_budget
    assert store.task(owner, task.id).state == 'failed'


@pytest.mark.parametrize('changes', [{'state': 'revoked'}, {'revoked_at': 1.}, {'id': './foo'}, {'created_by': ''}])
def test_project_domain_rejects_unversioned_identity_or_inconsistent_revocation(registry, changes):
    from flycode.domain.project import CanonicalProject

    store, root = registry
    project = projects(store).register(root, actor_id='owner')
    with pytest.raises(ValueError):
        CanonicalProject.model_validate({**project.model_dump(), **changes})
