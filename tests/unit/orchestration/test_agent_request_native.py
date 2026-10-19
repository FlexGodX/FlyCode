"""An HTTP pull starts real stages on an independently owned Host process."""
# ruff: noqa: F401,F811

import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from test_application_coordinator import native_app
from test_managed_coordinator import managed_pipeline, until

from flycode.domain.agent_requests import AgentTaskRequestReceipt
from flycode.domain.orchestration import BudgetAmount
from flycode.orchestration.integration import Integration
from flycode.orchestration.store import Store

HOST = r'''
import json,signal,sys
from pathlib import Path
from threading import Event
from flycode.orchestration.host import Host
from flycode.orchestration.attach import AttachmentServer
from flycode.orchestration.coordinator import Coordinator
stop=Event()
for name in (signal.SIGINT,signal.SIGTERM):
    signal.signal(name,lambda *_:stop.set())
with Host(Path(sys.argv[1]),Path(sys.argv[2])) as host, AttachmentServer(host):
    print(json.dumps({'session':host.session.id}),flush=True)
    assert sys.stdin.readline() == 'start\n'
    Coordinator(host).run(stop.is_set)
'''


@contextmanager
def waiting_host(database, root, endpoint):
    import flycode

    environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent),
        'LMSTUDIO_BASE_URL': endpoint}
    child = subprocess.Popen([sys.executable, '-c', HOST, str(database), str(root)], env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 15)[0], 'Host did not report readiness'
        ready = child.stdout.readline()
        assert ready, 'Host exited before readiness'
        yield child, json.loads(ready)['session']
    finally:
        if child.poll() is None:
            child.stdin.write('start\n')
            child.stdin.flush()
            child.terminate()
        try:
            stdout, stderr = child.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0 and not stderr, stdout + stderr


def test_http_request_survives_client_close_and_runs_native_worker(managed_pipeline, model_server, tmp_path, monkeypatch):
    store, owner, runtime, workflow, _, source, host_root, root, _, _, approve = managed_pipeline
    approve()
    instance = next(item for item in store.instances(runtime) if store._profile(item.profile_id).role == 'developer')
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'empty-artifacts'))
    original = model_server.respond
    model_entered, release_model = Event(), Event()
    writes = []

    def respond(request):
        connection = Store(store.path)
        try:
            with connection.transaction():
                connection.db.execute("UPDATE counters SET value=value WHERE name='agent_requests_schema'")
            writes.append(True)
        finally:
            connection.close()
        model_entered.set()
        assert release_model.wait(15), 'Owner did not release the actual HTTP inference'
        return original(request)

    model_server.respond = respond
    with waiting_host(store.path, host_root, model_server.url) as (host, session):
        with native_app(source, tmp_path / 'native-ui') as (_, web):
            project = workflow.project_id
            base = '/api/agents/' + instance.id
            response = web.post(base + '/task-request-context', json={'project_id': project, 'project_revision': 1,
                'expected_instance_revision': instance.revision, 'idempotency_key': 'native-context'})
            assert response.status_code == 200, response.text
            issued = response.json()['result']['issued']
            assert issued['context']['host_session_id'] == session
            web.headers.pop('X-FlyCode-Token')
            web.headers['X-FlyCode-Agent-Credential'] = issued['credential']
            body = {'expected_instance_revision': instance.revision, 'idempotency_key': 'native-pull', 'capabilities': ['python']}
            response = web.post(base + '/task-requests', json=body)
            assert response.status_code == 202, response.text
            request_id = response.json()['result']['receipt']['id']
            host.stdin.write('start\n')
            host.stdin.flush()
            try:
                until(model_entered.is_set, timeout=15)
                began = time.monotonic()
                response = web.post(base + '/task-requests', json=body)
                assert response.status_code == 200, response.text
                receipt = response.json()['result']['receipt']
                assert receipt['state'] == 'assigned' and receipt['result']['attempt']['instance_id'] == instance.id
                assert web.get(base + '/task-requests/' + request_id).json() == response.json()
                assert time.monotonic() - began < 2, 'Status/replay blocked on inference'
                deadline = store.workflow(owner, workflow.id).deadline_at
                assert deadline is not None
            finally:
                release_model.set()
        until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=45)
        assert host.poll() is None and writes
        saved = AgentTaskRequestReceipt.model_validate_json(store.db.execute(
            'SELECT data FROM agent_task_requests WHERE id=?', (request_id,)).fetchone()[0])
        assert saved.result.attempt.id == receipt['result']['attempt']['id']
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 3 and all(item.stop_confirmed for item in attempts)
        budget = store.budget(owner, workflow.id)
        assert budget.reserved == BudgetAmount() and not budget.uncertain
        assert store.workflow(owner, workflow.id).deadline_at == deadline
        identity = store.db.execute('SELECT id FROM integrations').fetchone()[0]
        assert Integration(store, root).current(owner, identity)
        assert (source / 'src/parser.py').read_text() == 'value = 1\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        time.sleep(.7)
        commands = tuple(map(tuple, store.db.execute('SELECT * FROM commands')))
        time.sleep(.8)
        assert tuple(map(tuple, store.db.execute('SELECT * FROM commands'))) == commands
