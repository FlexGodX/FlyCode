"""Model requests acquire a durable endpoint slot before any HTTP effect."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest
from openai import APIStatusError, APITimeoutError, OpenAI

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
from flycode.llm.catalogue import ModelCatalogue
from flycode.orchestration.store import Store, StoreError


@pytest.fixture
def active(tmp_path, model_server, request):
    options = dict(getattr(request, 'param', {}))
    if options.pop('_uppercase_scheme', False):
        model_server.url = model_server.url.replace('http:', 'HTTP:', 1)
    model_server.instance_ids = options
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store = Store(tmp_path / 'resources.sqlite', catalogue=catalogue)
    owner = AccessScope(actor_id='owner', project_ids={'project'}, role='owner')
    runtime = AccessScope(actor_id='host', project_ids={'project'}, role='runtime')
    permission = ExecutionPermission(approved=True, tools=('read_file',), read_scopes=('.',))
    acceptance = AcceptanceContract(id='contract', version=1, criteria=[AcceptanceCriterion(
        id='repair', check_ids=['regression'], expected_case_ids=['tests/test_parser.py::test_parse'])])
    draft = TaskDraft(title='Inspect parser', objective='Inspect the parser.', acceptance=acceptance,
        permissions=permission, budget=BudgetAmount(model_calls=2, tool_calls=4))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='project', task=draft,
                                    budget=WorkflowBudget()), key='workflow')
    def enqueue(task):
        return store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
                                                        idempotency_key='enqueue-' + task.id))
    enqueue(store.task(owner, workflow.root_task_id))
    tasks = [enqueue(store.create_task(owner, workflow.id,
        draft.model_copy(update={'parent_id': workflow.root_task_id}), key=f'task-{i}')) for i in range(2)]
    store.start_next(owner, key='start')
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Inspect only.', project_ids=('project',), permissions=permission, context_limit=8192))
    attempts = []
    for task in tasks:
        instance = store.register_instance(runtime, profile.id)
        attempt = store.claim(runtime, ClaimRequest(instance_id=instance.id,
            expected_instance_revision=instance.revision, task_id=task.id, expected_task_revision=task.revision,
            reserve=BudgetAmount(model_calls=2, tool_calls=4), idempotency_key='claim-' + task.id)).attempt
        attempts.append(store.confirm_started(runtime, attempt.id, attempt.fence))
    yield store, owner, runtime, catalogue, workflow, attempts, model
    store.close()


def test_two_connections_cannot_reserve_the_same_endpoint_or_double_charge(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, _ = active
    barrier = Barrier(2)
    def compete(attempt):
        connection = Store(store.path, catalogue=catalogue)
        try:
            barrier.wait(timeout=3)
            return Resources(connection).begin(runtime, attempt.id, attempt.fence, key=attempt.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, attempts))
    admitted = [result for result in results if result is not None]
    assert len(admitted) == 1 and admitted[0].fresh
    call = admitted[0].call
    replay = Resources(store).begin(runtime, call.attempt_id, call.fence, key=call.attempt_id)
    assert not replay.fresh and replay.call.id == call.id
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store.budget(owner, workflow.id).reserved.model_calls == 3
    assert sum(event.kind == 'model_start' for event in store.events(owner, workflow.id)) == 1


async def test_real_requests_are_serial_while_wait_and_stop_remain_responsive(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    entered, release, stop = Event(), Event(), Event()
    lock = Lock()
    counts = {'active': 0, 'maximum': 0}
    def response(request):
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
        entered.set()
        assert release.wait(timeout=5)
        with lock:
            counts['active'] -= 1
        return model_server.completion('[]')
    model_server.respond = response
    def invoke(attempt, stopping):
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempt.id, attempt.fence, key=attempt.id,
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the parser without editing.'}]),
                    should_stop=stopping.is_set)
        finally:
            connection.close()
    first = asyncio.create_task(asyncio.to_thread(invoke, attempts[0], Event()))
    assert await asyncio.to_thread(entered.wait, 3)
    second = asyncio.create_task(asyncio.to_thread(invoke, attempts[1], stop))
    try:
        deadline = time.monotonic() + 3
        while not any(e.kind == 'model_wait' for e in store.events(owner, workflow.id)):
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        stop.set()
        with pytest.raises(InterruptedError):
            await asyncio.wait_for(second, .5)
        assert not first.done() and len(model_server.requests) == 2  # probe + first request
        release.set()
        await asyncio.wait_for(first, 3)
        assert counts['maximum'] == 1
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 1 and ledger.reserved.model_calls == 3
        records = Resources(store).calls(owner, workflow.id)
        assert len(records) == 1 and records[0].status == 'completed'
        assert records[0].usage.total_tokens == 11
        await asyncio.to_thread(invoke, attempts[1], Event())
        assert len(model_server.requests) == 3 and counts['maximum'] == 1
        assert store.budget(owner, workflow.id).spent.model_calls == 2
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize('role', ['primary', 'scorer'])
async def test_default_standalone_clients_wait_for_canonical_endpoint_and_cancel_without_debit(active, model_server, monkeypatch, tmp_path, role):
    from flycode.domain import CandidateAction, Observation, TaskSpec, ToolAction
    from flycode.llm.lmstudio import make_lmstudio_cortex
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.environment import AttemptEnvironment
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    entered, release, second_http, waiting, stop = Event(), Event(), Event(), Event(), Event()
    lock = Lock()
    counts = {'active': 0, 'maximum': 0}
    def response(request):
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
            (second_http if entered.is_set() else entered).set()
        try:
            assert release.wait(10)
            return model_server.completion(json.dumps({'candidates': [
                {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}), [('A', -.1), ('B', -2.)])
        finally:
            with lock:
                counts['active'] -= 1
    model_server.respond = response
    def canonical():
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempts[0].id, attempts[0].fence, key='held-canonical',
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the registered source.'}]))
        finally:
            connection.close()
    first = asyncio.create_task(asyncio.to_thread(canonical))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        cortex = make_lmstudio_cortex(base_url=model_server.url, model='reasoner', decision_model='decider',
            should_stop=stop.is_set, on_event=lambda kind, data: waiting.set() if kind == 'model_wait' else None)
        context = CortexContext(task='Inspect the source.', behavior_mode='explore',
            observation=Observation(task='Inspect'))
        client = cortex.reasoner if role == 'primary' else cortex.decider
        def standalone():
            return (client.generate_candidates(context) if role == 'primary' else
                client.score(context, [CandidateAction(id='a', tool='read_file'), CandidateAction(id='b', tool='list_files')]))
        second = asyncio.create_task(asyncio.to_thread(standalone))
        deadline = time.monotonic() + 3
        while not (waiting.is_set() or second_http.is_set()):
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        assert waiting.is_set() and not second_http.is_set(), 'Standalone HTTP bypassed the canonical endpoint owner'
        assert client.request_count == 0
        root = tmp_path / 'independent-copy'
        root.mkdir()
        (root / 'sample.py').write_text('answer = 1\n')
        environment = AttemptEnvironment(root, store, runtime, attempts[1].id, attempts[1].fence)
        await environment.reset(TaskSpec(id=attempts[1].task_id, task='Inspect source.'))
        result = await environment.step(ToolAction(id='independent-file-read', tool='read_file', arguments={'path': 'sample.py'}))
        assert result.success and result.payload['content'] == 'answer = 1\n'
        stop.set()
        with pytest.raises(InterruptedError):
            await asyncio.wait_for(second, .5)
        assert client.request_count == 0 and len(model_server.requests) == 2
        assert store.budget(owner, workflow.id).spent == BudgetAmount(model_calls=1, tool_calls=1)
        release.set()
        await asyncio.wait_for(first, 3)
        stop.clear()
        assert await asyncio.to_thread(standalone)
        assert client.request_count == 1 and counts['maximum'] == 1 and len(model_server.requests) == 3
        assert len(Resources(store).calls(owner, workflow.id)) == 1
    finally:
        release.set()
        stop.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)


@pytest.mark.parametrize('role', ['primary', 'scorer'])
async def test_explicit_probe_cannot_bypass_canonical_endpoint_or_charge_before_admission(active, model_server, monkeypatch, role):
    from flycode.llm.catalogue import CatalogueBusy
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    entered, release, second_http = Event(), Event(), Event()
    def response(request):
        (second_http if entered.is_set() else entered).set()
        assert release.wait(10)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'read_file'}]}), [('A', -.1), ('B', -2.)])
    model_server.respond = response
    def canonical():
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempts[0].id, attempts[0].fence, key='held-probe-owner',
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect source.'}]))
        finally:
            connection.close()
    first = asyncio.create_task(asyncio.to_thread(canonical))
    probe = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        independent = ModelCatalogue(model_server.url)
        entry = independent.discover()[0]
        probe = asyncio.create_task(asyncio.to_thread(independent.probe, entry.model_ref, role))
        deadline = time.monotonic() + 3
        while not (probe.done() or second_http.is_set()):
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        assert not second_http.is_set(), 'A compatibility probe bypassed the canonical endpoint owner'
        with pytest.raises(CatalogueBusy):
            await probe
        assert independent.entries[entry.model_ref].probe_calls == 0 and not independent.entries[entry.model_ref].roles
        assert len(model_server.requests) == 2 and store.budget(owner, workflow.id).spent.model_calls == 1
        release.set()
        await asyncio.wait_for(first, 3)
        accepted = await asyncio.to_thread(independent.probe, entry.model_ref, role)
        assert accepted.status == 'ready' and accepted.roles == [role] and accepted.probe_calls == 1
        assert len(model_server.requests) == 3
    finally:
        release.set()
        await asyncio.gather(first, *([probe] if probe is not None else []), return_exceptions=True)


async def test_standalone_late_response_is_audited_but_not_returned(active, model_server, monkeypatch):
    from flycode.domain import Observation
    from flycode.llm.lmstudio import make_lmstudio_cortex
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    entered, release, stop = Event(), Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(10)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'read_file'}]}))
    model_server.respond = response
    cortex = make_lmstudio_cortex(model_server.url, model='reasoner', decision_model=None, should_stop=stop.is_set)
    context = CortexContext(task='Inspect source.', behavior_mode='explore', observation=Observation(task='Inspect source.'))
    pending = asyncio.create_task(asyncio.to_thread(cortex.reasoner.generate_candidates, context))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        stop.set()
        assert Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='while-stopped-http') is None
        release.set()
        with pytest.raises(InterruptedError, match='audit only'):
            await asyncio.wait_for(pending, 3)
        row = store.db.execute('SELECT state,data FROM model_calls WHERE attempt_id IS NULL').fetchone()
        call = json.loads(row['data'])
        assert row['state'] == 'completed' and call['late'] and call['usage']['total_tokens'] == 11
        assert cortex.reasoner.request_count == 1 and len(model_server.requests) == 2
        assert store.budget(owner, workflow.id).spent == BudgetAmount()
        assert Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='after-finished-http').fresh
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -1.])
def test_host_wait_limit_is_validated_before_database_or_http_effects(tmp_path, model_server, timeout):
    from flycode.orchestration.transport import HostTransport

    database = tmp_path / 'invalid-host.sqlite'
    transport = HostTransport(model_server.url, 'reasoner', 'primary', database=database)
    with OpenAI(base_url=model_server.url, api_key='local', max_retries=0) as client, pytest.raises(ValueError):
        transport.execute(lambda: client.chat.completions.create(model='reasoner',
            messages=[{'role': 'user', 'content': 'Inspect source.'}]), timeout=timeout)
    assert not model_server.requests and not database.exists()


@pytest.mark.parametrize('active', [{'_uppercase_scheme': True}], indirect=True)
@pytest.mark.parametrize('from_environment', [False, True])
def test_direct_primary_retains_configured_endpoint_identity_when_sdk_normalizes_url(active, model_server, monkeypatch, from_environment):
    from flycode.domain import Observation
    from flycode.llm.openai_compatible import OpenAICompatibleLLM
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.transport import EndpointBusy

    store, owner, runtime, _, workflow, attempts, _ = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    monkeypatch.setenv('OPENAI_BASE_URL', model_server.url)
    Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='configured-endpoint-owner')
    client = OpenAICompatibleLLM(model='reasoner', base_url=None if from_environment else model_server.url,
        api_key='local', timeout=.05)
    try:
        with pytest.raises(EndpointBusy):
            client.generate_candidates(CortexContext(task='Inspect source.', behavior_mode='explore',
                observation=Observation(task='Inspect')))
        assert client.request_count == 0 and len(model_server.requests) == 1
        assert store.budget(owner, workflow.id).spent.model_calls == 1
    finally:
        client._client.close()


def test_web_probe_reports_shared_capacity_without_bypassing_session_or_origin(active, model_server, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from flycode.orchestration.resources import Resources
    from flycode.web.app import create_app

    store, owner, runtime, _, workflow, attempts, _ = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    admission = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='web-probe-owner')
    app = create_app(tmp_path, tmp_path / 'ui', tmp_path / 'empty-artifact-catalogue')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        token = client.get('/api/bootstrap').json()['token']
        entry = client.get('/api/models').json()['catalogue'][0]
        body = {'model_ref': entry['model_ref'], 'role': 'primary'}
        assert client.post('/api/models/probe', json=body).status_code == 403
        client.headers['X-FlyCode-Token'] = token
        assert client.post('/api/models/probe', json=body, headers={'Origin': 'https://foreign.example'}).status_code == 403
        assert client.post('/api/models/probe', json=body).status_code == 409
        current = next(item for item in client.get('/api/models').json()['catalogue'] if item['model_ref'] == entry['model_ref'])
        assert current['probe_calls'] == 0 and not current['roles'] and current['status'] != 'unsupported'
        assert len(model_server.requests) == 1
        Resources(store).finish(runtime, admission.call.id, status='failed', error_class='TestAdmissionReleasedBeforeHTTP')
        accepted = client.post('/api/models/probe', json=body)
        assert accepted.status_code == 200
        assert accepted.json()['status'] == 'ready' and accepted.json()['probe_calls'] == 1
        assert len(model_server.requests) == 2
        assert store.budget(owner, workflow.id).spent.model_calls == 1


def test_host_unknown_transport_survives_restart_and_requires_exact_acknowledgement(active, model_server, monkeypatch):
    from flycode.domain import Observation
    from flycode.domain.orchestration import HostModelCall
    from flycode.llm.openai_compatible import OpenAICompatibleLLM
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.transport import HostTransport

    store, owner, runtime, catalogue, workflow, attempts, _ = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    release, provider_finished = Event(), Event()
    def response(request):
        assert release.wait(5)
        provider_finished.set()
        return model_server.completion(json.dumps({'candidates': [{'tool': 'read_file'}]}))
    model_server.respond = response
    client = OpenAICompatibleLLM(model='reasoner', base_url=model_server.url, api_key='local', timeout=.05)
    context = CortexContext(task='Inspect source.', behavior_mode='explore', observation=Observation(task='Inspect'))
    try:
        with pytest.raises(APITimeoutError):
            client.generate_candidates(context)
        row = store.db.execute('SELECT data FROM model_calls WHERE attempt_id IS NULL').fetchone()
        call = HostModelCall.model_validate_json(row['data'])
        assert call.status == 'uncertain' and call.ended_at is None and client.request_count == 1
        with pytest.raises(StoreError, match='scope'):
            Resources(store).get(owner, call.id)
        with pytest.raises(StoreError, match='scope'):
            Resources(store).finish(runtime, call.id, status='failed')
        connection = Store(store.path, catalogue=catalogue)
        try:
            assert Resources(connection).begin(runtime, attempts[0].id, attempts[0].fence, key='after-host-restart') is None
            assert connection.budget(owner, workflow.id).spent.model_calls == 0
        finally:
            connection.close()
        foreign = HostTransport(model_server.url, 'reasoner', 'primary', owner_id='another-host')
        with pytest.raises(StoreError, match='scope'):
            foreign.finish(call.id, status='failed')
        assert store.db.execute('SELECT state FROM model_calls WHERE id=?', (call.id,)).fetchone()['state'] == 'uncertain'
        release.set()
        assert provider_finished.wait(3)
        trusted = HostTransport(model_server.url, 'reasoner', 'primary', owner_id=call.owner_id)
        settled = trusted.finish(call.id, status='failed', error_class='ProviderFinishedAfterClientTimeout')
        assert settled.status == 'failed' and settled.usage is None
        assert trusted.finish(call.id, status='failed', error_class='ProviderFinishedAfterClientTimeout') == settled
        with pytest.raises(StoreError, match='reconcile_required'):
            trusted.finish(call.id, status='completed')
        assert Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='after-trusted-host-ack').fresh
        assert client.request_count == 1 and len(model_server.requests) == 2
    finally:
        release.set()
        client._client.close()


def legacy_model_calls_schema(store):
    """Install the literal schema9 format while preserving the test's real rows."""
    with store.transaction():
        store.db.execute('ALTER TABLE model_calls RENAME TO model_calls_current')
        store.db.execute('DROP INDEX active_endpoint')
        store.db.execute('''CREATE TABLE model_calls (
            id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
            endpoint_ref TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)''')
        store.db.execute('''INSERT INTO model_calls(rowid,id,attempt_id,endpoint_ref,state,data)
            SELECT rowid,id,attempt_id,endpoint_ref,state,data FROM model_calls_current''')
        store.db.execute('DROP TABLE model_calls_current')
        store.db.execute("CREATE UNIQUE INDEX active_endpoint ON model_calls(endpoint_ref) WHERE state IN ('running','uncertain')")
        store.db.execute('PRAGMA user_version=9')


def test_schema9_upgrade_preserves_active_calls_and_then_admits_typed_host_probe(active, model_server, monkeypatch):
    from flycode.llm.catalogue import CatalogueBusy
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, _ = active
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    first = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='migration-first')
    Resources(store).finish(runtime, first.call.id, status='completed')
    current = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='migration-held')
    Resources(store).finish(runtime, current.call.id, status='uncertain', error_class='InjectedTransportUncertainty')
    legacy_model_calls_schema(store)
    before = Resources(store).calls(owner, workflow.id), store.budget(owner, workflow.id), store.events(owner, workflow.id)
    connection = Store(store.path, catalogue=catalogue)
    try:
        assert (Resources(connection).calls(owner, workflow.id), connection.budget(owner, workflow.id),
            connection.events(owner, workflow.id)) == before
        assert Resources(connection).begin(runtime, attempts[1].id, attempts[1].fence, key='migration-no-bypass') is None
        independent = ModelCatalogue(model_server.url)
        entry = independent.discover()[0]
        with pytest.raises(CatalogueBusy):
            independent.probe(entry.model_ref, 'primary')
        assert len(model_server.requests) == 1
        Resources(connection).finish(runtime, current.call.id, status='failed', error_class='TrustedTestTransportAcknowledgement')
        accepted = independent.probe(entry.model_ref, 'primary')
        assert accepted.status == 'ready' and accepted.probe_calls == 1
        row = connection.db.execute('SELECT state,data FROM model_calls WHERE attempt_id IS NULL').fetchone()
        assert row['state'] == 'completed' and json.loads(row['data'])['owner_kind'] == 'probe'
        assert connection.budget(owner, workflow.id) == before[1]
        assert not connection.db.execute('PRAGMA foreign_key_check').fetchall()
        assert len(Resources(connection).calls(owner, workflow.id)) == 2 and len(model_server.requests) == 2
    finally:
        connection.close()


def test_schema9_migration_failure_rolls_back_live_endpoint_and_version(active, monkeypatch):
    import sqlite3

    from flycode.orchestration import store as module
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, _ = active
    held = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='migration-rollback-held')
    legacy_model_calls_schema(store)
    before = Resources(store).calls(owner, workflow.id), store.budget(owner, workflow.id), store.events(owner, workflow.id)
    connect = module.sqlite3.connect
    def denied_migration(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, first, second, database, trigger:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and first == 'model_calls_previous' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(module.sqlite3, 'connect', denied_migration)
        with pytest.raises(sqlite3.DatabaseError):
            Store(store.path, catalogue=catalogue)
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 9
    assert (Resources(store).calls(owner, workflow.id), store.budget(owner, workflow.id), store.events(owner, workflow.id)) == before
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='model_calls_previous'").fetchone()
    connection = Store(store.path, catalogue=catalogue)
    try:
        assert Resources(connection).get(owner, held.call.id) == held.call
        assert Resources(connection).begin(runtime, attempts[1].id, attempts[1].fence, key='after-rollback') is None
        assert not connection.db.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        connection.close()


def test_http_failure_is_charged_and_replay_never_sends_a_second_request(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    resources = Resources(store)
    model_server.respond = lambda request: (400, {'error': {'message': 'Rejected', 'type': 'invalid_request_error'}})
    with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
        invoke = lambda: client.chat.completions.create(model=model.model_id,
                    messages=[{'role': 'user', 'content': 'Inspect the parser.'}])
        with pytest.raises(APIStatusError):
            resources.execute(runtime, attempts[0].id, attempts[0].fence, key='bad-request', request=invoke)
        with pytest.raises(StoreError, match='request_replay'):
            resources.execute(runtime, attempts[0].id, attempts[0].fence, key='bad-request', request=invoke)
        assert len(model_server.requests) == 2
        assert resources.calls(owner, workflow.id)[0].status == 'failed'
        model_server.respond = lambda request: model_server.completion('[]')
        resources.execute(runtime, attempts[1].id, attempts[1].fence, key='next-request', request=invoke)
    assert store.budget(owner, workflow.id).spent.model_calls == 2


def test_unknown_http_outcome_holds_endpoint_across_restart_and_blocks_stop_ack(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    release = Event()
    model_server.respond = lambda request: (release.wait(timeout=3), model_server.completion('[]'))[1]
    try:
        with (OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0, timeout=.05) as client,
              pytest.raises(APITimeoutError)):
            Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='timeout',
                request=lambda: client.chat.completions.create(model=model.model_id,
                    messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
        connection = Store(store.path, catalogue=catalogue)
        try:
            resources = Resources(connection)
            assert resources.calls(owner, workflow.id)[0].status == 'uncertain'
            assert resources.begin(runtime, attempts[1].id, attempts[1].fence, key='after-timeout') is None
            assert connection.budget(owner, workflow.id).uncertain
            with pytest.raises(StoreError, match='resource_busy'):
                connection.confirm_stopped(runtime, attempts[0].id, attempts[0].fence,
                                           outcome='failed', expenditure_confirmed=True)
            assert len(model_server.requests) == 2
        finally:
            connection.close()
    finally:
        release.set()


def test_primary_client_accounts_for_generation_and_consolidation_at_http_boundary(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.openai_compatible import OpenAICompatibleLLM
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    keys = iter(('generation', 'consolidation', 'over-budget'))
    client = OpenAICompatibleLLM(model=model.model_id, base_url=model_server.url, api_key='local-test',
        structured_output=True, request_executor=lambda request: Resources(store).execute(runtime,
            attempts[0].id, attempts[0].fence, key=next(keys), request=request))
    context = CortexContext(task='Inspect the parser.', behavior_mode='explore', observation=Observation(task='Inspect'))
    assert client.generate_candidates(context)[0].tool == 'read_file'
    client.consolidate(context, ['Inspected the parser.'])
    with pytest.raises(StoreError, match='budget_exhausted'):
        client.generate_candidates(context)
    assert len(model_server.requests) == 3 and client.request_count == 2
    assert store.budget(owner, workflow.id).spent.model_calls == 2
    assert len(Resources(store).calls(owner, workflow.id)) == 2


def test_http_does_not_hold_sqlite_transaction_and_metadata_edit_keeps_response_current(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    def response(request):
        connection = Store(store.path, catalogue=catalogue)
        try:
            task = connection.task(owner, attempts[0].task_id)
            connection.command(owner, task.id, TaskCommand(operation='set_metadata', expected_revision=task.revision,
                idempotency_key='during-http', changes={'title': 'Renamed during inference'}))
        finally:
            connection.close()
        return model_server.completion('[]')
    model_server.respond = response
    with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0, timeout=2) as client:
        Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='unlocked-http',
            request=lambda: client.chat.completions.create(model=model.model_id,
                messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
    assert store.task(owner, attempts[0].task_id).title == 'Renamed during inference'
    assert not Resources(store).calls(owner, workflow.id)[0].late


def test_outbox_failure_rolls_back_endpoint_and_debit(active):
    import sqlite3

    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    resources = Resources(store)
    store.db.execute("CREATE TEMP TRIGGER fail_model_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'disk'); END")
    with pytest.raises(sqlite3.IntegrityError):
        resources.begin(runtime, attempts[0].id, attempts[0].fence, key='atomic')
    assert resources.calls(owner, workflow.id) == []
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    store.db.execute('DROP TRIGGER fail_model_outbox')
    assert resources.begin(runtime, attempts[0].id, attempts[0].fence, key='atomic').fresh


def test_model_calls_and_replays_reject_reduced_scope(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    admission = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='scoped-call')
    reduced = runtime.model_copy(update={'project_ids': frozenset({'other'})})
    with pytest.raises(StoreError, match='scope'):
        Resources(store).begin(reduced, attempts[0].id, attempts[0].fence, key='scoped-call')
    with pytest.raises(StoreError, match='scope'):
        Resources(store).finish(reduced, admission.call.id, status='completed')
    with pytest.raises(StoreError, match='scope'):
        Resources(store).calls(reduced, workflow.id)
    assert store.budget(owner, workflow.id).spent.model_calls == 1


def test_invalid_provider_usage_does_not_leave_finished_http_holding_endpoint(active, model_server):
    from pydantic import ValidationError

    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    def response(request):
        data = model_server.completion('[]')
        data['usage']['total_tokens'] = -1
        return data
    model_server.respond = response
    with (OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client,
          pytest.raises(ValidationError)):
        Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='bad-usage',
            request=lambda: client.chat.completions.create(model=model.model_id,
                messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
    assert Resources(store).calls(owner, workflow.id)[0].status == 'failed'
    assert Resources(store).begin(runtime, attempts[1].id, attempts[1].fence, key='after-bad-usage').fresh


def test_restart_marks_open_transport_uncertain_without_replaying_or_freeing_endpoint(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    admission = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='crashed-request')
    store.recover(runtime)
    call = Resources(store).get(owner, admission.call.id)
    assert call.status == 'uncertain' and call.late
    assert call.ended_at is None and store.budget(owner, workflow.id).uncertain
    assert any(event.kind == 'model_uncertain' for event in store.events(owner, workflow.id))


async def test_real_response_after_fence_revocation_is_audited_and_never_returned(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    entered, release = Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(timeout=5)
        return model_server.completion('[]')
    model_server.respond = response
    def invoke():
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempts[0].id, attempts[0].fence, key='late-response',
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
        finally:
            connection.close()
    worker = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        store.recover(runtime)
        release.set()
        with pytest.raises(StoreError, match='fence'):
            await asyncio.wait_for(worker, 3)
        call = Resources(store).calls(owner, workflow.id)[0]
        assert call.status == 'completed' and call.late and call.usage.total_tokens == 11
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        store.confirm_stopped(runtime, attempts[0].id, attempts[0].fence,
                             outcome='retry_wait', expenditure_confirmed=True)
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


def test_attempt_cortex_binds_canonical_context_and_each_actual_request(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, runtime, _, workflow, attempts, _ = active
    cortex = AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence)
    context = CortexContext(task='Injected unrelated task', behavior_mode='explore',
        observation=Observation(task='Injected observation'), candidate_tools=['create_file'])
    assert cortex.generate_candidates(context)[0].tool == 'read_file'
    request = model_server.requests[-1]
    serialized = json.dumps(request)
    assert 'Inspect the parser.' in serialized and 'Inspect only.' in serialized
    assert 'Injected unrelated task' not in serialized and 'Injected observation' not in serialized
    assert set(request['response_format']['json_schema']['schema']['properties']['candidates']['items']
               ['properties']['tool']['enum']) == {'read_file', 'stop'}
    cortex.reset()
    assert cortex.generate_candidates(context)
    assert cortex.generate_candidates(context) == []
    assert sum(cortex.model_calls.values()) == 1  # reset cannot reset the shared ledger
    assert store.budget(owner, workflow.id).spent.model_calls == 2
    assert len(model_server.requests) == 3  # one explicit probe, two admitted calls


@pytest.mark.parametrize('caller_flag', [False, True])
def test_canonical_objective_occurs_once_in_actual_request(active, model_server, caller_flag):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, runtime, _, workflow, attempts, _ = active
    context = CortexContext(task='Untrusted task', behavior_mode='explore',
        observation=Observation(task='Untrusted observation', recent_errors=['preserve this error']))
    context = context.model_copy(update={'objective_embedded': caller_flag})
    before = context.model_dump()
    cortex = AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence)
    assert cortex.generate_candidates(context)
    prompt = model_server.requests[-1]['messages'][-1]['content']
    assert prompt.count('Inspect the parser.') == 1
    assert attempts[0].task_id in prompt and 'preserve this error' in prompt
    assert 'Untrusted task' not in prompt and 'Untrusted observation' not in prompt
    assert context.model_dump() == before
    assert store.budget(owner, workflow.id).spent.model_calls == 1


def test_attempt_cortex_rechecks_loaded_context_and_bounds_input_before_http(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, runtime, _, workflow, attempts, model = active
    cortex = AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence)
    context = CortexContext(task='Inspect', behavior_mode='explore', observation=Observation(task='Inspect'),
                            working_memory=['x' * 20000])
    assert cortex.generate_candidates(context) == []
    assert sum(cortex.model_calls.values()) == 0
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    context.working_memory = []
    model_server.contexts = {model.model_id: 2048}
    assert cortex.generate_candidates(context) == []
    assert sum(cortex.model_calls.values()) == 0
    assert len(model_server.requests) == 1


async def test_attempt_cortex_drives_actual_run_agent_on_copied_project(active, model_server, tmp_path):
    from flycode.agent import run_agent
    from flycode.brain import make_brain
    from flycode.domain import TaskSpec
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.environment import AttemptEnvironment
    from flycode.orchestration.resources import Resources
    from flycode.orchestration.tools import Tools
    from flycode.verification.snapshot import capture

    store, owner, runtime, _, workflow, attempts, _ = active
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'sample.py').write_text('VALUE = 1\n')
    (original / 'untracked.txt').write_text('Keep this file.\n')
    copied = tmp_path / 'copied'
    capture(original, copied)
    events = []
    brain = make_brain('heuristic_fsm', seed=1)
    brain.set_learning_mode(False)
    try:
        result = await run_agent(AttemptEnvironment(copied, store, runtime, attempts[0].id, attempts[0].fence), brain,
            AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence),
            TaskSpec(id=attempts[0].task_id, task='Inspect the parser.', max_steps=1, max_tool_calls=1),
            run_id=attempts[0].id, on_event=lambda kind, data: events.append((kind, data)))
    finally:
        brain.close()
    assert result.tool_calls == 1 and not result.success
    assert any(kind == 'tool_end' and data['action']['tool'] == 'read_file' for kind, data in events)
    call = Resources(store).calls(owner, workflow.id)[0]
    assert call.attempt_id == attempts[0].id and call.instance_id == attempts[0].instance_id
    assert call.status == 'completed' and call.usage.total_tokens == 11
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    tool = Tools(store).calls(owner, workflow.id)[0]
    assert tool.status == 'completed' and tool.tool == 'read_file'
    assert tool.attempt_id == attempts[0].id and tool.instance_id == attempts[0].instance_id
    assert (original / 'sample.py').read_text() == 'VALUE = 1\n'
    assert (original / 'untracked.txt').read_text() == 'Keep this file.\n'


@pytest.mark.parametrize('active', [{'decider': 'opaque-loaded-instance'}], indirect=True)
def test_attempt_cortex_uses_loaded_identity_in_http_and_canonical_event(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    context = CortexContext(task='Inspect', behavior_mode='explore', observation=Observation(task='Inspect'))
    assert AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence).generate_candidates(context)
    assert model_server.requests[-1]['model'] == 'opaque-loaded-instance'
    assert Resources(store).calls(owner, workflow.id)[0].model_id == 'opaque-loaded-instance'


def test_attempt_cortex_rejects_sql_transaction_before_native_metadata_or_inference(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, _, runtime, _, _, attempts, _ = active
    cortex = AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence)
    context = CortexContext(task='Inspect', behavior_mode='explore', observation=Observation(task='Inspect'))
    reads, calls = len(model_server.get_requests), len(model_server.requests)
    with store.transaction(), pytest.raises(RuntimeError, match='transaction'):
        cortex.generate_candidates(context)
    assert len(model_server.get_requests) == reads and len(model_server.requests) == calls


def test_canonical_candidate_ids_are_unique_across_generations(active):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, _, runtime, _, _, attempts, _ = active
    cortex = AttemptCortex(store, runtime, attempts[0].id, attempts[0].fence)
    context = CortexContext(task='Inspect parser.', behavior_mode='explore', observation=Observation(task='inspect'))
    first, second = cortex.generate_candidates(context), cortex.generate_candidates(context)
    assert first[0].id != second[0].id
