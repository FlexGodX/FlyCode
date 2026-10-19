"""Only explicit owner edges authorize automatic task communication."""
# ruff: noqa: F401,F811

import pytest
from test_owner_command_attachment import approve_request, preview_request
from test_owner_recipe_profiles import (
    live_parent,
    managed_pipeline,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)

from flycode.domain.orchestration import AgentProfileDraft, TaskCommand
from flycode.orchestration.owner_commands import OwnerCommands
from flycode.orchestration.store import changed


@pytest.fixture
def communication_parent(pinned_parent):
    p = pinned_parent
    store, owner = p['store'], p['owner']
    old = store._profile(p['child'].allowed_profile_ids[0])
    permission = changed(old.permissions, tools=(*old.permissions.tools, 'send_message'))
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **old.model_dump(include=set(AgentProfileDraft.model_fields)), 'permissions': permission}))
    for name in ('parent', 'child'):
        task = store.task(owner, p[name].id)
        binding = p['dispatcher'].binding(owner, task.id)
        task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='enable-messages-' + name, changes={
                'allowed_profile_ids': (profile.id,), 'permissions': permission.model_dump(mode='json')}))
        p['dispatcher'].bind(owner, task.id, p['source'], expected_revision=task.revision,
            expected_binding_id=binding.id, key='message-binding-' + name, brain='heuristic_fsm')
        p[name] = task
    approval = OwnerCommands(store).execute(approve_request(p))
    assert approval.error_code is None, approval
    assert approval.result.kind == 'approved'
    p['pipeline_id'] = approval.result.pipeline_id
    return p


def request(p, *, key='approve-routes', head=None, edges=None):
    store, owner = p['store'], p['owner']
    base = preview_request(p)
    base.pop('workflow_id')
    return {**base, 'operation': 'message_routes', 'pipeline_id': p['pipeline_id'],
        'expected_revision': store.workflow(owner, p['workflow'].id).revision,
        'task_revisions': {task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
        'expected_grant_id': head, 'idempotency_key': key,
        'edges': edges if edges is not None else [{'sender_task_id': p['parent'].id, 'recipient_task_id': p['child'].id}]}


def execution_state(p):
    store, owner, workflow = p['store'], p['owner'], p['workflow']
    return (store.workflow(owner, workflow.id), store.tasks(owner, workflow.id), store.attempts(owner, workflow.id),
        store.budget(owner, workflow.id), tuple(map(tuple, store.db.execute('SELECT * FROM model_calls'))),
        tuple(map(tuple, store.db.execute('SELECT * FROM tool_calls'))),
        tuple(map(tuple, store.db.execute('SELECT * FROM pipelines'))))


def test_explicit_edges_replay_and_revoke_never_start_or_expand_work(communication_parent):
    p, service = communication_parent, OwnerCommands(communication_parent['store'])
    before = execution_state(p)
    payload = request(p)
    first = service.execute(payload)
    assert first.error_code is None, first
    assert first.result.kind == 'message_routes'
    assert first.result.grant_id and first.result.active
    assert [edge.model_dump() for edge in first.result.edges] == payload['edges']
    assert service.execute(payload) == first
    revoke = service.execute(request(p, key='revoke-routes', head=first.result.grant_id, edges=[]))
    assert revoke.error_code is None and revoke.result.edges == () and not revoke.result.active
    assert service.execute(payload) == first
    preview = service.execute({**preview_request(p), 'operation': 'message_routes_preview'})
    assert preview.error_code is None and preview.result.grant_id == revoke.result.grant_id
    assert not preview.result.active and preview.result.edges == ()
    assert execution_state(p) == before
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 2


@pytest.mark.parametrize('change,code', [
    ({'actor_id': 'owner'}, 'invalid_fields'),
    ({'expected_revision': 999}, 'revision'),
    ({'task_revisions': {}}, 'invalid_fields'),
    ({'expected_grant_id': 'f' * 32}, 'message_route_revision'),
    ({'pipeline_id': 'f' * 32}, 'not_found'),
])
def test_invalid_owner_route_intent_publishes_nothing(communication_parent, change, code):
    p = communication_parent
    before = execution_state(p)
    outcome = OwnerCommands(p['store']).execute({**request(p), **change})
    assert outcome.error_code == code, outcome
    assert execution_state(p) == before
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['self', 'duplicate', 'unknown', 'task_revision', 'fanout'])
def test_bad_edge_or_task_cas_is_rejected(communication_parent, kind):
    p = communication_parent
    payload = request(p)
    expected = 'invalid_fields'
    if kind == 'self':
        payload['edges'][0]['recipient_task_id'] = p['parent'].id
    elif kind == 'duplicate':
        payload['edges'] *= 2
    elif kind == 'fanout':
        payload['edges'] = [{'sender_task_id': p['parent'].id, 'recipient_task_id': value * 32} for value in 'bcde']
    elif kind == 'unknown':
        payload['edges'][0]['recipient_task_id'] = 'f' * 32
        expected = 'message_route_scope'
    else:
        payload['task_revisions'][p['child'].id] += 1
        expected = 'revision'
    outcome = OwnerCommands(p['store']).execute(payload)
    assert outcome.error_code == expected, outcome
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0


def test_route_never_grants_missing_send_permission(pinned_parent):
    p = pinned_parent
    service = OwnerCommands(p['store'])
    approved = service.execute(approve_request(p))
    assert approved.error_code is None, approved
    p['pipeline_id'] = approved.result.pipeline_id
    outcome = service.execute(request(p))
    assert outcome.error_code == 'permission', outcome
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0


def test_head_cas_and_changed_idempotency_cannot_restore_old_routes(communication_parent):
    p, service = communication_parent, OwnerCommands(communication_parent['store'])
    payload = request(p)
    first = service.execute(payload)
    assert first.error_code is None, first
    changed_payload = request(p, head=first.result.grant_id, edges=[])
    assert service.execute(changed_payload).error_code == 'idempotency'
    assert service.execute(request(p, key='stale-head')).error_code == 'message_route_revision'
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 1


def test_racing_owner_updates_have_one_winner(communication_parent):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.store import Store

    p = communication_parent
    barrier = Barrier(2)
    payloads = [request(p, key='race-left'), request(p, key='race-right', edges=[])]

    def approve(payload):
        store = Store(p['store'].path, catalogue=p['store'].catalogue, host=p['store'].host)
        try:
            service = OwnerCommands(store)
            barrier.wait(timeout=5)
            return service.execute(payload)
        finally:
            store.close()

    before = execution_state(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, payload) for payload in payloads]
        outcomes = [future.result(timeout=15) for future in futures]
    assert sum(outcome.error_code is None for outcome in outcomes) == 1
    assert [outcome.error_code for outcome in outcomes if outcome.error_code] == ['message_route_revision']
    assert execution_state(p) == before
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 1


def test_reopen_retains_exact_grant_and_head_bytes(communication_parent):
    from flycode.orchestration.store import Store

    p = communication_parent
    first = OwnerCommands(p['store']).execute(request(p))
    assert first.error_code is None
    tables = ('message_route_grants', 'message_route_heads', 'events', 'outbox', 'commands')
    before = {name: tuple(map(tuple, p['store'].db.execute('SELECT * FROM ' + name))) for name in tables}
    reopened = Store(p['store'].path, host=p['store'].host)
    try:
        assert {name: tuple(map(tuple, reopened.db.execute('SELECT * FROM ' + name))) for name in tables} == before
        assert OwnerCommands(reopened).execute(request(p)) == first
    finally:
        reopened.close()


def test_route_preview_never_constructs_execution_or_grants_project(communication_parent, monkeypatch):
    from flycode.orchestration.dispatch import Dispatcher
    from flycode.orchestration.pipeline import Pipelines

    p = communication_parent
    before = execution_state(p)
    grants = tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants')))
    for cls in (Dispatcher, Pipelines):
        monkeypatch.setattr(cls, '__init__', lambda *args, **kwargs: pytest.fail('Preview constructed execution'))
    result = OwnerCommands(p['store']).execute({**preview_request(p), 'operation': 'message_routes_preview'})
    assert result.error_code is None and result.result.grant_id is None
    assert not result.result.active
    assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants'))) == grants
    assert execution_state(p) == before


def test_route_cannot_cross_registered_project(communication_parent, tmp_path):
    from flycode.orchestration.projects import Projects

    p = communication_parent
    foreign = tmp_path / 'another-project'
    foreign.mkdir()
    project = Projects(p['store']).register(foreign, actor_id='owner')
    before = execution_state(p)
    result = OwnerCommands(p['store']).execute({**request(p), 'project_id': project.id, 'project_revision': project.revision})
    assert result.error_code == 'scope', result
    assert execution_state(p) == before
    assert p['store'].db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0


def test_paused_workflow_still_allows_explicit_revocation(communication_parent):
    p, store = communication_parent, communication_parent['store']
    service = OwnerCommands(store)
    grant = service.execute(request(p))
    assert grant.error_code is None
    parent = store.task(p['owner'], p['parent'].id)
    store.command(p['owner'], parent.id, TaskCommand(operation='pause', expected_revision=parent.revision,
        idempotency_key='pause-before-revoke'))
    result = service.execute(request(p, head=grant.result.grant_id, key='paused-revoke', edges=[]))
    assert result.error_code is None and not result.result.active and result.result.edges == ()


@pytest.mark.parametrize('corruption', ['json_workflow', 'sql_project', 'revoked_edges', 'old_head'])
def test_corrupt_grant_or_head_is_never_projected_as_owner_authority(communication_parent, corruption):
    import json

    p, store = communication_parent, communication_parent['store']
    service = OwnerCommands(store)
    payload = request(p)
    first = service.execute(payload)
    assert first.error_code is None
    grant_id = first.result.grant_id
    if corruption in {'revoked_edges', 'old_head'}:
        revoked = service.execute(request(p, head=grant_id, key='revoke-before-tamper', edges=[]))
        assert revoked.error_code is None
        if corruption == 'old_head':
            store.db.execute('UPDATE message_route_heads SET grant_id=? WHERE workflow_id=?',
                (grant_id, p['workflow'].id))
        else:
            row = store.db.execute('SELECT data FROM message_route_grants WHERE id=?', (revoked.result.grant_id,)).fetchone()
            data = json.loads(row['data'])
            data['edges'] = payload['edges']
            store.db.execute('UPDATE message_route_grants SET data=? WHERE id=?', (json.dumps(data), revoked.result.grant_id))
    elif corruption == 'json_workflow':
        row = store.db.execute('SELECT data FROM message_route_grants WHERE id=?', (grant_id,)).fetchone()
        data = json.loads(row['data'])
        data['workflow_id'] = 'f' * 32
        store.db.execute('UPDATE message_route_grants SET data=? WHERE id=?', (json.dumps(data), grant_id))
    else:
        from flycode.orchestration.projects import Projects

        foreign = p['source'].parent / 'foreign-route-project'
        foreign.mkdir()
        project = Projects(store).register(foreign, actor_id='owner')
        store.db.execute('UPDATE message_route_grants SET project_id=? WHERE id=?', (project.id, grant_id))
    result = service.execute({**preview_request(p), 'operation': 'message_routes_preview'})
    assert result.error_code == 'message_route_changed', result
    assert result.result is None


def test_valid_revoke_between_preview_reads_is_not_corruption(communication_parent):
    from flycode.orchestration.store import Store

    p, store = communication_parent, communication_parent['store']
    service = OwnerCommands(store)
    first = service.execute(request(p))
    assert first.error_code is None
    revoke = request(p, key='revoke-during-preview', head=first.result.grant_id, edges=[])
    connection = Store(store.path, host=store.host)
    original, fired = store.db, []

    class ReadOnce:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Interleaved:
        def __getattr__(self, name):
            return getattr(original, name)

        def execute(self, sql, parameters=()):
            cursor = original.execute(sql, parameters)
            if not fired and sql.lstrip().startswith('SELECT') and 'message_route_heads' in sql:
                row = cursor.fetchone()
                cursor.close()
                fired.append(True)
                assert OwnerCommands(connection).execute(revoke).error_code is None
                return ReadOnce(row)
            return cursor

    store.db = Interleaved()
    try:
        result = service.execute({**preview_request(p), 'operation': 'message_routes_preview'})
        assert fired and result.error_code is None, result
    finally:
        store.db = original
        connection.close()


@pytest.mark.parametrize('field', ['grant_id', 'actor_id'])
def test_malformed_approval_event_has_typed_rejection(communication_parent, field):
    import json

    p, store = communication_parent, communication_parent['store']
    service = OwnerCommands(store)
    assert service.execute(request(p)).error_code is None
    row = store.db.execute("SELECT sequence,data FROM events WHERE json_extract(data,'$.kind')='message_routes_approved'").fetchone()
    data = json.loads(row['data'])
    if field == 'grant_id':
        data['data'].pop(field)
    else:
        data.pop(field)
    store.db.execute('UPDATE events SET data=? WHERE sequence=?', (json.dumps(data), row['sequence']))
    result = service.execute({**preview_request(p), 'operation': 'message_routes_preview'})
    assert result.error_code == 'message_route_changed', result


def test_http_route_preview_and_mutation_use_current_host_and_selected_project(communication_parent, tmp_path):
    from test_application_coordinator import native_app

    from flycode.orchestration.attach import AttachmentServer

    p, store = communication_parent, communication_parent['store']
    grants = tuple(map(tuple, store.db.execute('SELECT * FROM project_grants')))
    preview = {**preview_request(p), 'operation': 'message_routes_preview'}
    url = '/api/tasks/owner/' + p['workflow'].id + '/message-routes'
    with AttachmentServer(store.host), native_app(p['source'], tmp_path / 'route-web-history') as (_, web):
        response = web.get(url, params={'project_id': preview['project_id']})
        assert response.status_code == 200, response.text
        assert response.json()['result']['grant_id'] is None
        assert web.post('/api/tasks/owner', json=preview).status_code == 200
        assert tuple(map(tuple, store.db.execute('SELECT * FROM project_grants'))) == grants
        payload = request(p)
        for headers in ({'X-FlyCode-Token': 'wrong'}, {'Origin': 'https://foreign.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            response = web.post('/api/tasks/owner', json=payload, headers=headers)
            assert response.status_code == 403, response.text
        assert web.get(url, params={'project_id': 'f' * 32}).status_code == 403
        assert web.post('/api/tasks/owner', json={**payload, 'actor_id': 'owner'}).status_code == 422
        assert store.db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0
        response = web.post('/api/tasks/owner', json=payload)
        assert response.status_code == 200, response.text
        first = response.json()['result']
        assert first['active'] and first['edges'] == payload['edges']
        assert web.post('/api/tasks/owner', json=payload).json()['result'] == first
        revoked = web.post('/api/tasks/owner', json=request(p, key='web-revoke', head=first['grant_id'], edges=[]))
        assert revoked.status_code == 200 and not revoked.json()['result']['active']
        assert web.get(url, params={'project_id': preview['project_id']}).json()['result']['grant_id'] == revoked.json()['result']['grant_id']
    assert not store.attempts(p['owner'], p['workflow'].id)


def test_host_lost_after_owner_project_preflight_cannot_approve_routes(communication_parent, monkeypatch):
    from test_host_leadership import replace_host_lock

    p, store = communication_parent, communication_parent['store']
    payload = request(p)
    before = store.outbox_count()
    project = OwnerCommands._project

    def lose_host(service, command):
        current = project(service, command)
        replace_host_lock(store.host)
        return current

    monkeypatch.setattr(OwnerCommands, '_project', lose_host)
    outcome = OwnerCommands(store).execute(payload)
    assert outcome.error_code is not None and outcome.error_code.startswith('host_'), outcome
    assert store.outbox_count() == before
    assert store.db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 0
