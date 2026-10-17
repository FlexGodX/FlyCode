"""Real application processes share one detached Host and protected commands."""

import json
import os
import select
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import test_managed_coordinator
from test_managed_coordinator import until

from flycode.orchestration.host import Host
from flycode.orchestration.store import Store, default_state_path
from flycode.orchestration.tools import Tools
from flycode.orchestration.wire import receive, send
from flycode.verification.custody import stopped

managed_pipeline = test_managed_coordinator.managed_pipeline

APP = r'''
import json, os, socket, subprocess, sys, time
from pathlib import Path
from threading import Thread
import uvicorn
from flycode.web.app import create_app
control = os.environ.get('TEST_COORDINATOR_CONTROL_FD')
if control:
    original = subprocess.Popen
    wrapper = """
import os, runpy, signal, socket, sys
from threading import Thread
from flycode.orchestration.wire import receive, send
control = socket.socket(fileno=int(os.environ['TEST_COORDINATOR_CONTROL_FD']))
control.set_inheritable(False)
send(control, {'pid': os.getpid(), 'sid': os.getsid(0)})
def stop_self():
    try:
        if receive(control) == {'stop': True}:
            os.kill(os.getpid(), signal.SIGTERM)
    except EOFError:
        pass
Thread(target=stop_self, daemon=True).start()
sys.argv = ['flycode.orchestration.coordinator', *sys.argv[1:]]
runpy.run_module('flycode.orchestration.coordinator', run_name='__main__')
"""
    def launch(args, *values, **options):
        if isinstance(args, list) and 'flycode.orchestration.coordinator' in args:
            args = [args[0], '-c', wrapper, *args[3:]]
            options['pass_fds'] = (int(control),)
        return original(args, *values, **options)
    subprocess.Popen = launch
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
sock.listen()
server = uvicorn.Server(uvicorn.Config(create_app(Path(sys.argv[1]), Path(sys.argv[2])),
    log_level='error', timeout_graceful_shutdown=2))
thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
thread.start()
try:
    deadline = time.monotonic() + 8
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(.01)
    print(json.dumps({'url': 'http://127.0.0.1:' + str(sock.getsockname()[1])}), flush=True)
    sys.stdin.readline()
finally:
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    assert not thread.is_alive()
'''


@contextmanager
def native_app(root, history, *, control=None):
    import flycode

    environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)}
    options = {}
    if control is not None:
        environment['TEST_COORDINATOR_CONTROL_FD'] = str(control.fileno())
        options['pass_fds'] = (control.fileno(),)
    process = subprocess.Popen([sys.executable, '-c', APP, str(root), str(history)], env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **options)
    try:
        assert select.select([process.stdout], [], [], 10)[0], 'Owned application did not report readiness'
        line = process.stdout.readline()
        assert line, 'Owned application exited before readiness'
        address = json.loads(line)['url']
        with httpx.Client(base_url=address, timeout=15) as client:
            client.headers['X-FlyCode-Token'] = client.get('/api/bootstrap').json()['token']
            yield process, client
    finally:
        if process.poll() is None:
            process.stdin.write('close\n')
            process.stdin.flush()
        try:
            _stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0 and not stderr, stderr


@contextmanager
def owned_service_control():
    """Test-only inherited channel asks the exact daemon to signal itself."""
    parent, child = socket.socketpair()
    parent.settimeout(30)
    try:
        yield parent, child
    finally:
        child.close()
        try:
            send(parent, {'stop': True})
            while parent.recv(4096):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            parent.close()


def test_detached_host_survives_actual_launching_application_process_exit(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'empty-artifacts'))
    with Host(default_state_path(), tmp_path / 'hosts'):
        pass
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'owner.txt').write_bytes(b'owner\x00\xff')
    with owned_service_control() as (control, inherited):
        with native_app(source, tmp_path / 'history-a', control=inherited) as (first, client):
            inherited.close()
            response = client.post('/api/tasks/coordinator/attach', json={})
            assert response.status_code == 200, response.text
            status = response.json()
            identity = receive(control)
            assert identity['pid'] == identity['sid'] and identity['pid'] != first.pid
        assert first.returncode == 0
        with native_app(source, tmp_path / 'history-b') as (_, other):
            assert other.get('/api/tasks/coordinator').json() == status
            assert other.post('/api/tasks/coordinator/attach', json={}).json() == status
        assert (source / 'owner.txt').read_bytes() == b'owner\x00\xff'
    store = Store(default_state_path())
    try:
        assert json.loads(store.db.execute('SELECT data FROM host_sessions').fetchone()[0])['phase'] == 'stopped'
        for table in ('tasks', 'attempts', 'model_calls', 'tool_calls'):
            assert store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize('managed_pipeline', ['slow-review', 'slow-integration'], indirect=True)
@pytest.mark.parametrize('root_loss', [False, True])
def test_other_application_pauses_real_verification_after_first_process_exits(managed_pipeline, model_server,
                                                                             tmp_path, monkeypatch, root_loss):
    store, owner, _, workflow, tasks, source, _, _, _, _, approve = managed_pipeline
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    monkeypatch.setenv('FLYCODE_ARTIFACT_ROOT', str(tmp_path / 'empty-artifacts'))
    approve()
    with owned_service_control() as (control, inherited):
        with native_app(source, tmp_path / 'history-a', control=inherited) as (first, client):
            inherited.close()
            response = client.post('/api/tasks/coordinator/attach', json={})
            assert response.status_code == 200, response.text
            status = response.json()
            receive(control)
        assert first.returncode == 0
        with native_app(source, tmp_path / 'history-b') as (_, other):
            assert other.get('/api/tasks/coordinator').json() == status
            review = source.parent / 'review-started'
            integration = source.parent / 'integration-started'
            until(lambda: review.exists() or integration.exists(), timeout=45)
            task = tasks[3] if integration.exists() else tasks[2]
            budget = store.budget(owner, workflow.id)
            deadline = store.workflow(owner, workflow.id).deadline_at
            card = other.get('/api/tasks/' + task.id)
            assert card.status_code == 200, card.text
            original = source
            if root_loss:
                original = source.with_name('displaced-original')
                source.rename(original)
                source.mkdir()
                (source / 'replacement.txt').write_text('Replacement must stay untouched')
                assert other.get('/api/tasks/' + task.id).status_code == 403
            before_calls = len(model_server.requests)
            started = time.monotonic()
            paused = other.post('/api/tasks/' + task.id + ('/stop' if root_loss else '/commands'), json={
                'operation': 'pause', 'expected_revision': card.json()['task']['revision'],
                'idempotency_key': 'stop-from-second-process'})
            assert paused.status_code == 200, paused.text
            assert time.monotonic() - started < 1
            until(lambda: any(call.status == 'uncertain' for call in Tools(store).calls(owner, workflow.id)), timeout=10)
            call = next(call for call in Tools(store).calls(owner, workflow.id) if call.status == 'uncertain')
            assert call.process is not None
            def confirmed_stop():
                try:
                    with stopped(call.process) as receipt:
                        return receipt.started and receipt.stop_confirmed
                except BlockingIOError:
                    return False
            until(confirmed_stop, timeout=10)
            assert store.budget(owner, workflow.id).spent == budget.spent
            assert store.budget(owner, workflow.id).reserved.tool_calls > 0
            assert store.workflow(owner, workflow.id).deadline_at == deadline
            assert len(model_server.requests) == before_calls
            assert (original / 'src/parser.py').read_text() == 'value = 0\n'
            assert (original / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
            if root_loss:
                assert (source / 'replacement.txt').read_text() == 'Replacement must stay untouched'
