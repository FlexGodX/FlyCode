"""Owner commands use the current live Host without granting work on preview."""
# ruff: noqa: F811

import json
import shutil
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_owner_recipe_profiles import (  # noqa: F401
    alter_profile,
    live_parent,
    managed_pipeline,
    parent_ready,
    pinned_parent,
    pipeline_ready,
    state,
    wait_handles,
)
from test_pipeline_retries import retry_ready  # noqa: F401

from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.owner_commands import ACTOR, OwnerCommands
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import StoreError
from flycode.orchestration.wire import receive, send


@pytest.fixture(autouse=True)
def owner_endpoint(model_server, monkeypatch):
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)


def preview_request(p):
    project = Projects(p['store']).get(p['parent'].project_id)
    return {'operation': 'preview', 'project_id': project.id, 'project_revision': project.revision,
        'workflow_id': p['workflow'].id}


def test_owner_preview_is_authenticated_read_only_and_uses_existing_host(pinned_parent):
    p = pinned_parent
    before = state(p)
    commands = tuple(map(tuple, p['store'].db.execute('SELECT * FROM commands')))
    grants = tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants')))
    with AttachmentServer(p['store'].host), CoordinatorClient(p['store'].path) as client:
        assert callable(getattr(client, 'owner', None)), 'Authenticated owner command transport is missing'
        outcome = client.owner(preview_request(p))
        assert outcome.error_code is None
        assert outcome.result.kind == 'preview'
        assert outcome.result.workflow_id == p['workflow'].id
        assert {task.id for task in outcome.result.tasks} == set(p['approvals'])
        assert {profile.id for profile in outcome.result.profiles} == {profile.id for profile in p['profile_pins'].values()}
        assert client.status().session_id == p['store'].host.session.id
    assert state(p) == before
    assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM commands'))) == commands
    assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants'))) == grants


def test_preview_never_constructs_execution_or_pipeline_storage(pinned_parent, monkeypatch):
    p = pinned_parent
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only preview constructed execution storage')
    monkeypatch.setattr(Dispatcher, '__init__', forbidden)
    monkeypatch.setattr(Pipelines, '__init__', forbidden)
    service = OwnerCommands(p['store'])
    assert service.execute(preview_request(p)).result.kind == 'preview'


@pytest.mark.parametrize('operation', ['prepare', 'approve_parent'])
def test_host_revocation_after_preflight_prevents_new_binding_or_recipe(pinned_parent, monkeypatch, operation):
    import flycode.orchestration.dispatch as dispatch_module

    p = pinned_parent
    bindings = tuple(map(tuple, p['store'].db.execute('SELECT * FROM worker_bindings ORDER BY id')))
    revoked = []
    if operation == 'prepare':
        original = dispatch_module.capture
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            if not revoked:
                revoked.append(True)
                p['store'].host.drain()
            return result
        monkeypatch.setattr(dispatch_module, 'capture', capture)
        request = prepare_request(p)
    else:
        from flycode.orchestration.approval_profiles import ApprovalProfiles
        prepare = ApprovalProfiles.prepare
        def revoke(registered, *args, **kwargs):
            result = prepare(registered, *args, **kwargs)
            revoked.append(True)
            p['store'].host.drain()
            return result
        monkeypatch.setattr(ApprovalProfiles, 'prepare', revoke)
        request = approve_request(p)
    result = OwnerCommands(p['store']).execute(request)
    assert revoked and result.error_code.startswith('host_')
    assert not p['store'].db.execute('SELECT 1 FROM pipelines').fetchone()
    assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM worker_bindings ORDER BY id'))) == bindings
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_cli_owner_preview_and_explicit_prepare_share_rpc_receipts(pinned_parent, tmp_path):
    from test_tracker_cli import run_cli

    p = pinned_parent
    request_file = tmp_path / 'owner-command.json'
    request_file.write_text(json.dumps(preview_request(p)))
    with AttachmentServer(p['store'].host):
        arguments = ('--database', p['store'].path, 'owner', '--project', p['source'], '--input', request_file)
        preview = run_cli(*arguments)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        assert json.loads(preview.stdout)['result']['kind'] == 'preview'
        request_file.write_text(json.dumps(prepare_request(p)))
        prepared = run_cli(*arguments)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        assert json.loads(prepared.stdout)['result']['kind'] == 'prepared'
        again = run_cli(*arguments)
        assert again.returncode == 0 and json.loads(again.stdout) == json.loads(prepared.stdout)
        request_file.write_text(json.dumps({**preview_request(p), 'project_id': 'f' * 32}))
        denied = run_cli(*arguments)
        assert denied.returncode == 2 and json.loads(denied.stderr)['error']['code'] == 'scope'
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_http_owner_scope_token_origin_preview_and_explicit_prepare(pinned_parent, tmp_path):
    from test_application_coordinator import native_app

    p = pinned_parent
    before = state(p)
    grants = tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants')))
    with AttachmentServer(p['store'].host), native_app(p['source'], tmp_path / 'web-history') as (_, web):
        payload = preview_request(p)
        response = web.post('/api/tasks/owner', json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['result']['kind'] == 'preview'
        assert state(p) == before
        assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants'))) == grants
        response = web.get('/api/tasks/owner/' + p['workflow'].id, params={'project_id': payload['project_id']})
        assert response.status_code == 200 and response.json()['result']['kind'] == 'preview'
        assert tuple(map(tuple, p['store'].db.execute('SELECT * FROM project_grants'))) == grants
        for headers in ({'X-FlyCode-Token': 'wrong'}, {'Origin': 'https://foreign.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            response = web.post('/api/tasks/owner', json=payload, headers=headers)
            assert response.status_code == 403, response.text
        response = web.post('/api/tasks/owner', json={**payload, 'project_id': 'f' * 32})
        assert response.status_code == 403, response.text
        response = web.post('/api/tasks/owner', json={**payload, 'actor_id': 'owner'})
        assert response.status_code == 422, response.text
        assert state(p) == before
        response = web.post('/api/tasks/owner', json=prepare_request(p))
        assert response.status_code == 200, response.text
        assert response.json()['result']['kind'] == 'prepared'
        assert set(response.json()['result']['binding_ids']) == {p['parent'].id, p['child'].id}
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def prepare_request(p, key='prepare-owner-workers'):
    store, owner = p['store'], p['owner']
    return {**preview_request(p), 'operation': 'prepare', 'idempotency_key': key,
        'expected_revision': store.workflow(owner, p['workflow'].id).revision,
        'workers': {p[name].id: {'expected_revision': store.task(owner, p[name].id).revision,
            'expected_binding_id': p['dispatcher'].binding(owner, p[name].id).id, 'brain': 'heuristic_fsm'}
            for name in ('parent', 'child')}}


def approve_request(p, key='approve-owner-parent'):
    store, owner = p['store'], p['owner']
    return {**preview_request(p), 'operation': 'approve_parent', 'idempotency_key': key,
        'expected_revision': store.workflow(owner, p['workflow'].id).revision,
        'task_revisions': {task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
        'pairs': {p['parent'].id: p['parent_review'].id, p['child'].id: p['child_review'].id},
        'integrator_id': p['integrator'].id,
        'profiles': {identity: {'id': profile.id, 'version': profile.version} for identity, profile in p['profile_pins'].items()}}


def test_explicit_preparation_and_approval_replay_do_not_start_attempts(pinned_parent):
    p = pinned_parent
    prepare = prepare_request(p)
    old = {identity: worker['expected_binding_id'] for identity, worker in prepare['workers'].items()}
    shutil.rmtree(p['dispatcher'].root / 'verification/oracles')
    with AttachmentServer(p['store'].host), CoordinatorClient(p['store'].path) as client:
        first = client.owner(prepare)
        assert first.error_code is None and first.result.kind == 'prepared' and not first.result.partial
        assert set(first.result.binding_ids) == set(old)
        assert all(value != old[identity] for identity, value in first.result.binding_ids.items())
        after = state(p)
        assert client.owner(prepare) == first and state(p) == after
        assert not (p['dispatcher'].root / 'verification/oracles').exists()
        assert not p['store'].attempts(p['owner'], p['workflow'].id)
        approval = approve_request(p)
        approved = client.owner(approval)
        assert approved.error_code is None and approved.result.kind == 'approved'
        assert approved.result.graph_revision == 0
        after = state(p)
        assert client.owner(approval) == approved and state(p) == after
        altered = {**approval, 'preparation_steps': 2}
        assert client.owner(altered).error_code == 'idempotency'
        assert state(p) == after
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_partial_preparation_reports_completed_bindings_and_resumes_exact_intent(pinned_parent, monkeypatch):
    p = pinned_parent
    original, fail = Dispatcher.bind, [True]
    def partial(dispatcher, scope, identity, *args, **kwargs):
        if identity == p['child'].id and fail:
            fail.pop()
            raise StoreError('source_unavailable')
        return original(dispatcher, scope, identity, *args, **kwargs)
    monkeypatch.setattr(Dispatcher, 'bind', partial)
    request = prepare_request(p)
    with AttachmentServer(p['store'].host), CoordinatorClient(p['store'].path) as client:
        first = client.owner(request)
        assert first.error_code == 'source_unavailable' and first.result.partial
        assert set(first.result.binding_ids) == {p['parent'].id}
        second = client.owner(request)
        assert second.error_code is None and not second.result.partial
        assert second.result.binding_ids[p['parent'].id] == first.result.binding_ids[p['parent'].id]
        assert set(second.result.binding_ids) == {p['parent'].id, p['child'].id}
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_lost_recipe_reply_replays_frozen_profiles_after_supersession(pinned_parent, monkeypatch):
    p = pinned_parent
    original, lose = OwnerCommands._approve, [True]
    def lost(service, *args, **kwargs):
        result = original(service, *args, **kwargs)
        if lose:
            lose.pop()
            raise OSError('reply lost after committed recipe')
        return result
    monkeypatch.setattr(OwnerCommands, '_approve', lost)
    request = approve_request(p)
    with AttachmentServer(p['store'].host), CoordinatorClient(p['store'].path) as client:
        assert client.owner(request).error_code == 'owner_input_changed'
        pipeline = p['store'].db.execute('SELECT id FROM pipelines').fetchone()['id']
        alter_profile(p, p['profile_pins'][p['child'].id], 'head')
        before = state(p)
        replay = client.owner(request)
        assert replay.error_code is None and replay.result.pipeline_id == pipeline
        assert state(p) == before
        assert len(list(p['store'].db.execute('SELECT * FROM pipelines'))) == 1


def test_owner_receipt_survives_real_host_generation_change(pinned_parent, managed_pipeline):
    from flycode.orchestration.host import Host

    p = pinned_parent
    original_host = p['store'].host
    request = approve_request(p)
    with AttachmentServer(original_host), CoordinatorClient(p['store'].path) as first:
        receipt = first.owner(request)
        assert receipt.error_code is None
        generation = first.status().generation
    original_host.close()
    before = state(p)
    with Host(p['store'].path, managed_pipeline[6]) as replacement, AttachmentServer(replacement):
        with CoordinatorClient(p['store'].path) as second:
            assert second.status().generation == generation + 1
            assert second.owner(request) == receipt
            assert second.owner({**request, 'reruns': 1}).error_code == 'idempotency'
        assert state(p) == before
        assert not p['store'].attempts(p['owner'], p['workflow'].id)


@pytest.mark.parametrize('kind', ['proof', 'payload', 'session', 'schema', 'oversize'])
def test_raw_owner_frames_cannot_bypass_authentication_hash_or_schema(pinned_parent, kind):
    from flycode.orchestration.attach import LIMIT, OWNER_LIMIT, _payload_hash, _proof

    p = pinned_parent
    before = state(p)
    with AttachmentServer(p['store'].host) as server:
        descriptor = server.descriptor
        payload = prepare_request(p)
        if kind == 'schema':
            payload['actor_id'] = 'foreign-owner'
        body = {**descriptor.model_dump(include={'registration_id', 'session_id', 'generation', 'phase'}),
            'version': 'coordinator-attach-v1', 'operation': 'owner', 'challenge': 'a' * 64,
            'payload_hash': 'f' * 64 if kind == 'payload' else _payload_hash(payload)}
        if kind == 'session':
            body['session_id'] = 'f' * 32
        proof = '0' * 64 if kind == 'proof' else _proof(descriptor.auth_nonce, body, 'request')
        with socket.socket(socket.AF_UNIX) as channel:
            channel.settimeout(2)
            channel.connect(descriptor.socket_path)
            try:
                send(channel, {**body, 'proof': proof}, max_frame=LIMIT)
                if kind == 'oversize':
                    channel.sendall(struct.pack('!I', OWNER_LIMIT + 1))
                else:
                    send(channel, payload, max_frame=OWNER_LIMIT)
                if kind == 'schema':
                    reply = receive(channel, max_frame=OWNER_LIMIT)
                    assert reply['outcome'] == {'result': None, 'error_code': 'invalid_fields'}
                else:
                    with pytest.raises((EOFError, OSError)):
                        receive(channel, max_frame=OWNER_LIMIT)
            except (BrokenPipeError, ConnectionResetError):
                assert kind != 'schema'
        assert CoordinatorClient(p['store'].path).status().phase == 'ready'
    assert state(p) == before
    assert not p['store'].db.execute('SELECT 1 FROM commands WHERE actor_id=?', (ACTOR,)).fetchone()


@pytest.mark.parametrize('kind', ['workflow', 'task', 'profile', 'profile_version', 'project', 'actor', 'source', 'scope', 'hash'])
def test_invalid_owner_authority_and_stale_references_publish_nothing(pinned_parent, kind):
    p = pinned_parent
    request = approve_request(p)
    if kind == 'workflow':
        request['expected_revision'] += 1
    elif kind == 'task':
        request['task_revisions'][p['child'].id] += 1
    elif kind == 'profile':
        request['profiles'][p['child'].id]['id'] = 'f' * 32
    elif kind == 'profile_version':
        request['profiles'][p['child'].id]['version'] += 1
    elif kind == 'project':
        request['project_revision'] += 1
    else:
        request[{'actor': 'actor_id', 'source': 'source_root', 'scope': 'scope', 'hash': 'approval_hashes'}[kind]] = 'not-authority'
    before = state(p)
    with AttachmentServer(p['store'].host), CoordinatorClient(p['store'].path) as client:
        assert client.owner(request).error_code is not None
    assert state(p) == before
    assert not p['store'].db.execute('SELECT 1 FROM commands WHERE actor_id=?', (ACTOR,)).fetchone()


def test_status_and_stop_remain_responsive_during_preparation(pinned_parent, monkeypatch):
    from flycode.domain.orchestration import TaskCommand

    p = pinned_parent
    entered, release = Event(), Event()
    original = Dispatcher.bind
    def held(dispatcher, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(dispatcher, *args, **kwargs)
    monkeypatch.setattr(Dispatcher, 'bind', held)
    request = prepare_request(p)
    with AttachmentServer(p['store'].host), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(CoordinatorClient(p['store'].path).owner, request)
        try:
            assert entered.wait(5)
            start = time.monotonic()
            assert CoordinatorClient(p['store'].path).status().phase == 'ready'
            assert CoordinatorClient(p['store'].path).owner(preview_request(p)).error_code == 'owner_busy'
            task = p['store'].task(p['owner'], p['parent'].id)
            stopped = Projects(p['store']).stop_command(ACTOR, task.id, TaskCommand(operation='pause',
                expected_revision=task.revision, idempotency_key='stop-during-owner-prepare'))
            assert stopped.paused and time.monotonic() - start < 1
        finally:
            release.set()
        assert future.result(timeout=6).result.kind == 'prepared'
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_public_commands_reach_parent_wait_replan_fresh_review_and_current_integration(pinned_parent, model_server, monkeypatch):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.integration import Integration

    p = pinned_parent
    store, owner, dispatcher = p['store'], p['owner'], p['dispatcher']
    constructor = Pipelines.__init__
    def outside_sql(service, store, *args, **kwargs):
        assert not store.db.in_transaction, 'Pipeline storage constructed under the writer lock'
        constructor(service, store, *args, **kwargs)
    monkeypatch.setattr(Pipelines, '__init__', outside_sql)
    shutil.rmtree(dispatcher.root / 'verification/oracles')
    with AttachmentServer(store.host), CoordinatorClient(store.path) as client:
        assert client.owner(prepare_request(p)).error_code is None
        approved = client.owner(approve_request(p))
        assert approved.error_code is None
        first = dispatcher.tick(p['host'], key='rpc-parent-preparation')
        assert first.attempt.task_id == p['parent'].id
        wait_handles(dispatcher)
        assert Pipelines(store, dispatcher.root).advance(p['host'], p['workflow'].id) == ()
        before = store.budget(owner, p['workflow'].id)
        deadline = store.workflow(owner, p['workflow'].id).deadline_at
        objective = 'Repair the parser under the exact owner-approved criteria.'
        replan = {**preview_request(p), 'operation': 'replan', 'pipeline_id': approved.result.pipeline_id,
            'expected_revision': store.workflow(owner, p['workflow'].id).revision,
            'task_revisions': {task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
            'changes': {p['child'].id: {'objective': objective}}, 'profiles': approve_request(p)['profiles'],
            'idempotency_key': 'rpc-owner-replan'}
        replan.pop('workflow_id')
        revised = client.owner(replan)
        assert revised.error_code is None and revised.result.graph_revision == 1
        assert store.budget(owner, p['workflow'].id) == before
        assert client.owner(replan) == revised
        original = model_server.respond
        def respond(request):
            if p['parent'].objective in str(request['messages']):
                return original(request)
            assert objective in str(request['messages'])
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
                'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
        model_server.respond = respond
        for number in range(18):
            dispatcher.tick(p['host'], key=f'rpc-owner-delivery-{number}')
            wait_handles(dispatcher)
            assert store.workflow(owner, p['workflow'].id).deadline_at == deadline
            if store.workflow(owner, p['workflow'].id).state == 'succeeded':
                break
        assert store.workflow(owner, p['workflow'].id).state == 'succeeded'
        parent = store.task(owner, p['parent'].id)
        assert Integration(store, dispatcher.root).current(owner, parent.result_id)
        assert len(store.attempts(owner, p['workflow'].id)) == 6
        ledger = store.budget(owner, p['workflow'].id)
        assert ledger.spent.model_calls == 3 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        assert (p['source'] / 'src/parser.py').read_bytes() == b'value = 1\n'


@pytest.mark.parametrize('live_parent', ['retry'], indirect=True)
def test_public_retry_new_branch_and_host_restart_keep_original_budget_and_evidence(pinned_parent, managed_pipeline, model_server):
    from flycode.domain.orchestration import BudgetAmount, TaskCommand, TaskDraft
    from flycode.domain.verification_control import VerificationProfileDraft
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.host import Host
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.verification.registry import VerificationRegistry

    p = pinned_parent
    store, owner, dispatcher = p['store'], p['owner'], p['dispatcher']
    (p['source'] / 'src/second.py').write_text('value = 0\n')
    integrator = store.task(owner, p['integrator'].id)
    store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='public-addition-budget', changes={'budget': {'model_calls': 0, 'tool_calls': 35}}))
    approval = {**approve_request(p), 'retry_policies': p['retry_policies']}
    with AttachmentServer(store.host), CoordinatorClient(store.path) as client:
        assert client.owner(prepare_request(p)).error_code is None
        approved = client.owner(approval)
        assert approved.error_code is None
        for number in range(16):
            dispatcher.tick(p['host'], key=f'public-before-addition-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'succeeded':
                break
        assert store.task(owner, p['child_review'].id).state == 'succeeded'
        assert Pipelines(store, dispatcher.root).advance(p['host'], p['workflow'].id) == ()
        child = store.task(owner, p['child'].id)
        assert child.completion_id is not None
        attempts = [attempt for attempt in store.attempts(owner, p['workflow'].id) if attempt.task_id == child.id]
        assert len(attempts) == 2 and all(attempt.stop_confirmed for attempt in attempts)
        patches = [Artifacts(store, dispatcher.root).for_attempt(owner, attempt.id) for attempt in attempts]
        reviews = [Reviews(store, dispatcher.root).get(owner, row['id']) for patch in patches
            for row in store.db.execute('SELECT id FROM reviews WHERE artifact_id=?', (patch.id,))]
        assert {review.status for review in reviews} == {'changes_requested', 'approved'}
        assert (Artifacts(store, dispatcher.root).directory / patches[0].id / 'target/src/parser.py').read_text() == 'value = -1\n'
        deadline = store.workflow(owner, p['workflow'].id).deadline_at
        before = store.budget(owner, p['workflow'].id)
        contract = p['child'].acceptance.model_copy(update={'id': 'public-added-child'})
        oracle = p['source'].parent / 'public-addition-oracle'
        (oracle / 'tests').mkdir(parents=True)
        (oracle / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n'
            '    assert runpy.run_path("src/parser.py")["value"] == 1\n'
            '    assert runpy.run_path("src/second.py")["value"] == 2\n')
        profile = VerificationRegistry(store).register_profile(owner, child.project_id,
            VerificationProfileDraft(title='Explicit additional branch criteria', acceptance=contract,
                checks=p['profile_pins'][child.id].draft.checks), oracle_root=oracle)
        writer = TaskDraft.model_validate({**p['child'].model_dump(include=set(TaskDraft.model_fields)),
            'title': 'Additional owner branch', 'objective': 'Set second value to 2 after the verified parser repair.',
            'acceptance': contract, 'dependencies': (child.id,), 'budget': {'model_calls': 1, 'tool_calls': 1}})
        reviewer = TaskDraft.model_validate({**p['child_review'].model_dump(include=set(TaskDraft.model_fields)),
            'title': 'Review additional owner branch', 'acceptance': contract, 'budget': {'model_calls': 0, 'tool_calls': 4}})
        replan = {**preview_request(p), 'operation': 'replan', 'pipeline_id': approved.result.pipeline_id,
            'expected_revision': store.workflow(owner, p['workflow'].id).revision,
            'task_revisions': {task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
            'profiles': approve_request(p)['profiles'], 'idempotency_key': 'public-add-owner-branch',
            'new_branches': [{'writer': writer.model_dump(mode='json'), 'reviewer': reviewer.model_dump(mode='json'),
                'template_writer_id': child.id, 'profile': {'id': profile.id, 'version': profile.version}}]}
        replan.pop('workflow_id')
        revised = client.owner(replan)
        assert revised.error_code is None and len(revised.result.added_task_ids) == 2
        assert store.budget(owner, p['workflow'].id) == before
        assert store.task(owner, child.id).completion_id == child.completion_id
    original = model_server.respond
    def respond(request):
        if writer.objective not in str(request['messages']):
            return original(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/second.py', 'old_text': 'value = 0', 'new_text': 'value = 2'}}]}))
    model_server.respond = respond
    old_generation = store.host.session.generation
    store.host.close()
    with Host(store.path, managed_pipeline[6]) as replacement, AttachmentServer(replacement), CoordinatorClient(store.path) as client:
        connection = replacement.connect(catalogue=store.catalogue)
        restarted = Dispatcher(connection, dispatcher.root)
        try:
            assert client.status().generation == old_generation + 1
            assert client.owner(replan) == revised
            assert client.owner(approval) == approved
            for number in range(25):
                restarted.tick(p['host'], key=f'public-after-restart-{number}')
                wait_handles(restarted)
                assert connection.workflow(owner, p['workflow'].id).deadline_at == deadline
                if connection.workflow(owner, p['workflow'].id).state == 'succeeded':
                    break
            assert connection.workflow(owner, p['workflow'].id).state == 'succeeded'
            parent = connection.task(owner, p['parent'].id)
            assert Integration(connection, dispatcher.root).current(owner, parent.result_id)
            assert connection.task(owner, child.id).completion_id == child.completion_id
            ledger = connection.budget(owner, p['workflow'].id)
            assert ledger.spent.model_calls == 5 and ledger.reserved == BudgetAmount() and not ledger.uncertain
            assert len(connection.attempts(owner, p['workflow'].id)) == 10
            assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
            assert (p['source'] / 'src/parser.py').read_text() == 'value = 1\n'
            assert (p['source'] / 'src/extra.py').read_text() == 'value = 4\n'
            assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        finally:
            for handle in restarted.handles.values():
                handle.close()
            connection.close()
