"""Real local HTTP and Host RPC keep agent credentials separate from owner intent."""
# ruff: noqa: F401,F811

import pytest
from test_agent_task_requests import idle_requester, issue, managed_pipeline, payload
from test_application_coordinator import native_app

from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.attach import AttachmentServer

HEADER = 'X-FlyCode-Agent-Credential'


def context_body(p, **changes):
    return {'project_id': p[-2].id, 'project_revision': p[-2].revision,
        'expected_instance_revision': p[-1].revision, 'idempotency_key': 'http-idle-context', **changes}


@pytest.fixture
def request_http(idle_requester, tmp_path, monkeypatch, model_server):
    p = idle_requester
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(p[0].path))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'empty-artifacts'))
    with AttachmentServer(p[0].host), native_app(p[-2].root.path, tmp_path / 'request-ui') as (_, web):
        yield p, web


def test_http_owner_issues_context_and_agent_alone_submits_and_reads(request_http):
    p, web = request_http
    store, owner, _, workflow, _, instance = p
    base = '/api/agents/' + instance.id
    response = web.post(base + '/task-request-context', json=context_body(p))
    assert response.status_code == 200, response.text
    issued = response.json()['result']['issued']
    assert issued['context']['instance_id'] == instance.id
    assert web.post(base + '/task-request-context', json=context_body(p)).json() == response.json()
    web.headers.pop('X-FlyCode-Token')
    grants = tuple(map(tuple, store.db.execute('SELECT * FROM project_grants')))
    headers = {HEADER: issued['credential']}
    response = web.post(base + '/task-requests', headers=headers, json=payload(p).model_dump(mode='json'))
    assert response.status_code == 202, response.text
    receipt = response.json()['result']['receipt']
    assert receipt['state'] == 'queued'
    assert web.post(base + '/task-requests', headers=headers, json=payload(p).model_dump(mode='json')).json() == response.json()
    read = web.get(base + '/task-requests/' + receipt['id'], headers=headers)
    assert read.status_code == 200 and read.json() == response.json()
    assert read.headers['cache-control'] == 'no-store'
    assert tuple(map(tuple, store.db.execute('SELECT * FROM project_grants'))) == grants
    assert not store.attempts(owner, workflow.id)
    for table, column in [('commands', 'result'), ('events', 'data'), ('agent_task_requests', 'data')]:
        assert all(issued['credential'] not in row[0] for row in store.db.execute('SELECT ' + column + ' FROM ' + table))


@pytest.mark.parametrize('kind', ['owner_only', 'forged', 'swapped_instance', 'origin', 'cross_site', 'duplicate', 'oversized'])
def test_http_invalid_agent_authority_has_no_queue_or_budget_effect(request_http, kind):
    p, web = request_http
    store, owner, _, workflow, _, instance = p
    issued = issue(p)
    headers = [(HEADER, issued.credential)]
    identity = instance.id
    if kind == 'owner_only':
        headers = []
    else:
        web.headers.pop('X-FlyCode-Token')
    if kind == 'forged':
        headers = [(HEADER, issued.context.id + '.' + '0' * 64)]
    elif kind == 'swapped_instance':
        identity = 'f' * 32
    elif kind == 'origin':
        headers.append(('Origin', 'https://foreign.example'))
    elif kind == 'cross_site':
        headers.append(('Sec-Fetch-Site', 'cross-site'))
    elif kind == 'duplicate':
        headers.append((HEADER, issued.credential))
    elif kind == 'oversized':
        headers = [(HEADER, issued.credential + '0' * 10000)]
    before = store.budget(owner, workflow.id)
    response = web.post('/api/agents/' + identity + '/task-requests', headers=headers, json=payload(p).model_dump(mode='json'))
    assert response.status_code == 403, response.text
    assert issued.credential not in response.text
    assert not store.db.execute('SELECT 1 FROM agent_task_requests').fetchone()
    assert not store.attempts(owner, workflow.id) and store.budget(owner, workflow.id) == before


def test_agent_credential_does_not_replace_owner_token_on_other_routes(request_http):
    p, web = request_http
    issued = issue(p)
    web.headers['X-FlyCode-Token'] = issued.credential
    web.headers[HEADER] = issued.credential
    for path in ['/api/agents/activity', '/api/tasks', '/api/verification/plans']:
        assert web.get(path).status_code == 403
    base = '/api/agents/' + p[-1].id
    assert web.post(base + '/task-request-context', json=context_body(p)).status_code == 403
    assert web.post('/api/tasks/owner', json={}).status_code == 403
    for path in [base + '/task-requests/', base + '/task-requests/' + 'f' * 32 + '/extra']:
        assert web.post(path, json=payload(p).model_dump(mode='json')).status_code == 403
    assert web.head(base + '/task-requests/' + 'f' * 32).status_code == 403


def test_http_rejects_changed_replay_and_closed_fields(request_http):
    p, web = request_http
    issued = issue(p)
    web.headers.pop('X-FlyCode-Token')
    web.headers[HEADER] = issued.credential
    path = '/api/agents/' + p[-1].id + '/task-requests'
    body = payload(p).model_dump(mode='json')
    assert web.post(path, json=body).status_code == 202
    assert web.post(path, json={**body, 'available': False}).status_code == 409
    for changes in [{'role': 'owner'}, {'scope': '.'}, {'reserve': {}}, {'capabilities': ['x'] * 65}, {'available': 1}]:
        assert web.post(path, json={**body, **changes}).status_code == 422
    assert len(AgentTaskRequests(p[0]).pending()) == 1


def test_missing_agent_header_rejects_before_json_parsing(request_http):
    p, web = request_http
    web.headers.pop('X-FlyCode-Token')
    path = '/api/agents/' + p[-1].id + '/task-requests'
    for body in ['{', '{}', '[' * 10000]:
        response = web.post(path, content=body, headers={'Content-Type': 'application/json'})
        assert response.status_code == 403, response.text
        assert response.json() == {'detail': {'code': 'agent_identity'}}


def test_owner_validation_never_reflects_embedded_agent_credential(request_http):
    p, web = request_http
    issued = issue(p)
    body = {'operation': 'request_task', 'project_id': p[-2].id, 'project_revision': p[-2].revision,
        'instance_id': p[-1].id, 'credential': issued.credential}
    for value in [body, issued.credential, [body]]:
        response = web.post('/api/tasks/owner', json=value)
        assert response.status_code == 422, response.text
        assert issued.credential not in response.text


def test_agent_request_body_is_bounded_before_schema_validation(request_http):
    p, web = request_http
    issued = issue(p)
    web.headers.pop('X-FlyCode-Token')
    response = web.post('/api/agents/' + p[-1].id + '/task-requests',
        headers={HEADER: issued.credential, 'Content-Type': 'application/json'}, content=b' ' * (128 * 1024 + 1))
    assert response.status_code == 413, response.text
    assert not p[0].db.execute('SELECT 1 FROM agent_task_requests').fetchone()


def test_issued_agent_context_does_not_expand_selected_folders(request_http, tmp_path):
    p, _ = request_http
    issued = issue(p)
    root = tmp_path / 'other-selected-folder'
    root.mkdir()
    before = tuple(map(tuple, p[0].db.execute('SELECT * FROM project_grants')))
    with native_app(root, tmp_path / 'other-history') as (_, web):
        web.headers.pop('X-FlyCode-Token')
        response = web.post('/api/agents/' + p[-1].id + '/task-requests',
            headers={HEADER: issued.credential}, json=payload(p).model_dump(mode='json'))
        assert response.status_code == 403, response.text
    assert tuple(map(tuple, p[0].db.execute('SELECT * FROM project_grants'))) == before
    assert not AgentTaskRequests(p[0]).pending()


def test_expired_agent_context_cannot_submit_over_http(request_http):
    p, web = request_http
    issued = issue(p)
    p[0].host._agent_request_deadlines[issued.context.id] = 0.
    web.headers.pop('X-FlyCode-Token')
    response = web.post('/api/agents/' + p[-1].id + '/task-requests',
        headers={HEADER: issued.credential}, json=payload(p).model_dump(mode='json'))
    assert response.status_code == 403, response.text
    assert not AgentTaskRequests(p[0]).pending()


def test_no_work_receipt_and_backoff_are_readable_without_new_spend(request_http):
    from flycode.orchestration.coordinator import Coordinator

    p, web = request_http
    issued = issue(p)
    web.headers.pop('X-FlyCode-Token')
    web.headers[HEADER] = issued.credential
    path = '/api/agents/' + p[-1].id + '/task-requests'
    before = p[0].budget(p[1], p[3].id)
    response = web.post(path, json=payload(p).model_dump(mode='json'))
    request_id = response.json()['result']['receipt']['id']
    _, selected = Coordinator(p[0].host)._select(None)
    assert selected is None
    response = web.get(path + '/' + request_id)
    receipt = response.json()['result']['receipt']
    assert response.status_code == 200 and receipt['state'] == 'no_eligible_task'
    assert receipt['next_request_at'] == receipt['completed_at'] + 30.
    assert web.post(path, json=payload(p).model_dump(mode='json')).json() == response.json()
    response = web.post(path, json=payload(p, idempotency_key='fresh-too-soon').model_dump(mode='json'))
    assert response.status_code == 409 and response.json()['detail']['code'] == 'request_backoff'
    assert p[0].budget(p[1], p[3].id) == before
