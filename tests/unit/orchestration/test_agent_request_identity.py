"""Idle request credentials derive from Host authority, never from an agent ID."""
# ruff: noqa: F401,F811

import pytest
from test_managed_coordinator import managed_pipeline

from flycode.domain.orchestration import AccessScope
from flycode.orchestration.host import Host
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import StoreError, changed


@pytest.fixture
def idle_requester(managed_pipeline):
    store, owner, runtime, workflow, _, source, host_root, _, *_ = managed_pipeline
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        try:
            instance = next(i for i in connection.instances(runtime)
                if connection._profile(i.profile_id).role == 'developer')
            yield connection, owner, runtime, workflow, Projects(connection).resolve(source), instance
        finally:
            connection.close()


def issue(p, *, scope=None, revision=None, key='issue-idle-context'):
    from flycode.orchestration.agent_requests import AgentRequests

    store, owner, _, _, project, instance = p
    return AgentRequests(store).issue(scope or owner, project, instance_id=instance.id,
        expected_revision=instance.revision if revision is None else revision, key=key)


def test_issued_context_replay_is_inert_and_keeps_raw_secret_out_of_storage(idle_requester):
    from flycode.orchestration.agent_requests import AgentRequests

    p = idle_requester
    store, owner, _, workflow, project, instance = p
    before = store.budget(owner, workflow.id)
    issued = issue(p)
    assert issued.context.instance_id == instance.id and issued.context.project_id == project.id
    assert issued.context.profile_id == instance.profile_id and issued.context.instance_revision == instance.revision
    assert issued.context.host_generation == store.host.session.generation
    assert issued.context.expires_at == issued.context.created_at + 300
    assert AgentRequests(store).authorize(instance.id, issued.credential) == issued.context
    assert issue(p) == issued
    assert not store.attempts(owner, workflow.id) and store.budget(owner, workflow.id) == before
    assert store.db.execute('SELECT count(*) FROM model_calls WHERE attempt_id IS NOT NULL').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0
    for table in ('commands', 'events', 'agent_request_contexts'):
        assert all(issued.credential not in row[0] and issued.credential.split('.')[1] not in row[0]
            for row in store.db.execute('SELECT ' + ('result' if table == 'commands' else 'data') + ' FROM ' + table))


@pytest.mark.parametrize('kind', ['role', 'foreign', 'revision', 'busy', 'profile_version'])
def test_invalid_issuer_or_instance_cannot_create_context(idle_requester, kind):
    p = idle_requester
    store, owner, runtime, _, _, instance = p
    scope, revision, code = owner, None, 'agent_context_stale'
    if kind == 'role':
        scope, code = runtime, 'scope'
    elif kind == 'foreign':
        scope, code = AccessScope(actor_id='foreign', project_ids={'f' * 32}, role='owner'), 'scope'
    elif kind == 'revision':
        revision, code = instance.revision + 1, 'revision'
    elif kind == 'busy':
        store._save_instance(changed(instance, state='reconciling'))
    else:
        store._save_instance(changed(instance, profile_version=instance.profile_version + 1))
    with pytest.raises(StoreError, match=code):
        issue(p, scope=scope, revision=revision)
    assert store.db.execute('SELECT count(*) FROM agent_request_contexts').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['token', 'instance', 'expired', 'replaced', 'profile', 'project'])
def test_context_cannot_survive_changed_authority(idle_requester, kind):
    from flycode.orchestration.agent_requests import AgentRequests

    p = idle_requester
    store, _, _, _, project, instance = p
    issued = issue(p)
    credential, identity = issued.credential, instance.id
    if kind == 'token':
        credential = credential.split('.')[0] + '.' + '0' * 64
    elif kind == 'instance':
        identity = 'f' * 32
    elif kind == 'expired':
        store.clock = lambda: issued.context.expires_at
    elif kind == 'replaced':
        newer = issue(p, key='replace-idle-context')
        assert newer.context.id != issued.context.id
        assert AgentRequests(store).authorize(instance.id, newer.credential) == newer.context
    elif kind == 'profile':
        profile = store._profile(instance.profile_id)
        store.db.execute('UPDATE profiles SET data=? WHERE id=?',
            (changed(profile, instructions='Changed registered profile').model_dump_json(), profile.id))
    else:
        store.db.execute('UPDATE canonical_projects SET data=? WHERE id=?',
            (changed(project, revision=project.revision + 1).model_dump_json(), project.id))
    with pytest.raises(StoreError, match='agent_identity'):
        AgentRequests(store).authorize(identity, credential)


def test_old_issuance_replay_never_restores_replaced_context(idle_requester):
    from flycode.orchestration.agent_requests import AgentRequests

    p = idle_requester
    first, second = issue(p), issue(p, key='newer-owner-intent')
    assert issue(p) == first
    with pytest.raises(StoreError, match='agent_identity'):
        AgentRequests(p[0]).authorize(p[-1].id, first.credential)
    assert AgentRequests(p[0]).authorize(p[-1].id, second.credential) == second.context


def test_real_host_restart_revokes_old_credential_without_deleting_audit(managed_pipeline):
    from flycode.orchestration.agent_requests import AgentRequests

    store, owner, runtime, workflow, _, source, host_root, _, *_ = managed_pipeline
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        try:
            instance = connection.instances(runtime)[0]
            project = Projects(connection).resolve(source)
            first = AgentRequests(connection).issue(owner, project, instance_id=instance.id,
                expected_revision=instance.revision, key='before-restart')
            raw = connection.db.execute('SELECT data FROM agent_request_contexts WHERE id=?', (first.context.id,)).fetchone()[0]
        finally:
            connection.close()
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        try:
            service = AgentRequests(connection)
            with pytest.raises(StoreError, match='agent_identity'):
                service.authorize(instance.id, first.credential)
            with pytest.raises(StoreError, match='agent_context_stale'):
                service.issue(owner, project, instance_id=instance.id, expected_revision=instance.revision, key='before-restart')
            assert connection.db.execute('SELECT data FROM agent_request_contexts WHERE id=?', (first.context.id,)).fetchone()[0] == raw
            assert not connection.attempts(owner, workflow.id)
        finally:
            connection.close()


def test_host_loss_after_physical_project_preflight_cannot_issue(idle_requester, monkeypatch):
    p = idle_requester
    store = p[0]
    original = Projects.resolve
    entered = []

    def resolve(registry, *args, **kwargs):
        assert not registry.store.db.in_transaction
        result = original(registry, *args, **kwargs)
        entered.append(True)
        store.host.drain()
        return result

    monkeypatch.setattr(Projects, 'resolve', resolve)
    with pytest.raises(StoreError, match='host_'):
        issue(p)
    assert entered and store.db.execute('SELECT count(*) FROM agent_request_contexts').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['extend_expiry', 'head_rollback', 'clock_before_issue', 'observed_expiry_rollback'])
def test_immutable_issuance_and_observed_expiry_cannot_be_revived(idle_requester, kind):
    import json

    from flycode.orchestration.agent_requests import AgentRequests

    p = idle_requester
    store, _, _, _, _, instance = p
    first = issue(p)
    if kind == 'extend_expiry':
        value = first.context.model_dump(mode='json')
        value['expires_at'] += 600
        store.db.execute('UPDATE agent_request_contexts SET data=? WHERE id=?', (json.dumps(value), first.context.id))
        store.clock = lambda: first.context.expires_at + 1
    elif kind == 'head_rollback':
        issue(p, key='new-head-before-rollback')
        store.db.execute('UPDATE agent_request_heads SET context_id=? WHERE instance_id=?', (first.context.id, instance.id))
    elif kind == 'clock_before_issue':
        store.clock = lambda: first.context.created_at - 1
    else:
        store.clock = lambda: first.context.expires_at
        with pytest.raises(StoreError, match='agent_identity'):
            AgentRequests(store).authorize(instance.id, first.credential)
        store.clock = lambda: first.context.created_at + 1
    with pytest.raises(StoreError, match='agent_identity'):
        AgentRequests(store).authorize(instance.id, first.credential)


def test_replacement_in_another_project_cannot_restore_previous_head(idle_requester):
    from pathlib import Path

    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.orchestration.agent_requests import AgentRequests

    p = idle_requester
    store, owner, _, _, first_project, old_instance = p
    root = Path(first_project.root.path).parent / 'second-request-project'
    root.mkdir()
    second_project = Projects(store).register(root, actor_id=owner.actor_id)
    projects = {first_project.id, second_project.id}
    full_owner = AccessScope(actor_id=owner.actor_id, project_ids=projects, role='owner')
    runtime = AccessScope(actor_id='runtime', project_ids=projects, role='runtime')
    previous = store._profile(old_instance.profile_id)
    profile = store.register_profile(full_owner, AgentProfileDraft.model_validate({
        **previous.model_dump(include=set(AgentProfileDraft.model_fields)), 'project_ids': tuple(sorted(projects))}))
    instance = store.register_instance(runtime, profile.id)
    service = AgentRequests(store)
    first = service.issue(owner, first_project, instance_id=instance.id, expected_revision=instance.revision, key='first-project-context')
    second_owner = AccessScope(actor_id=owner.actor_id, project_ids={second_project.id}, role='owner')
    second = service.issue(second_owner, second_project, instance_id=instance.id,
        expected_revision=instance.revision, key='second-project-context')
    assert service.authorize(instance.id, second.credential) == second.context
    store.db.execute('UPDATE agent_request_heads SET context_id=? WHERE instance_id=?', (first.context.id, instance.id))
    with pytest.raises(StoreError, match='agent_identity'):
        service.authorize(instance.id, first.credential)
