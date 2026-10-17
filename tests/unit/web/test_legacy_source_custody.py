"""Legacy chats participate in shared source ownership and actual tool custody."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from test_app import wait_run

from flycode.web.app import create_app


def test_independent_managers_reject_same_source_before_model_request(tmp_path, model_server, monkeypatch):
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    workspace = tmp_path / 'workspace'
    source = workspace / 'project'
    source.mkdir(parents=True)
    (source / 'keep.txt').write_text('user bytes')
    (source / 'test_keep.py').write_text('def test_keep():\n    assert False\n')
    entered, release = Event(), Event()

    def respond(request):
        entered.set()
        assert release.wait(8)
        return model_server.completion(json.dumps({'candidates': [
            {'tool': 'create_file', 'arguments': {'path': 'late.txt', 'content': 'late'}}]}))

    model_server.respond = respond
    body = {'task': 'Preserve user files.', 'project': 'project', 'max_steps': 5,
            'model': 'reasoner', 'decision_model': None}
    catalogue = tmp_path / 'catalogue'
    with (TestClient(create_app(workspace, tmp_path / 'first', catalogue), base_url='http://localhost') as first,
          TestClient(create_app(workspace, tmp_path / 'second', catalogue), base_url='http://localhost') as second):
        for session in (first, second):
            session.headers['X-FlyCode-Token'] = session.get('/api/bootstrap').json()['token']
        run = first.post('/api/runs', json=body)
        assert run.status_code == 201, run.text
        other = None
        try:
            assert entered.wait(5)
            other = second.post('/api/runs', json=body)
            assert other.status_code == 409, other.text
            assert len(model_server.requests) == 1
            assert second.get('/api/runs').json() == []
        finally:
            first.post(f"/api/runs/{run.json()['id']}/stop")
            if other is not None and other.status_code == 201:
                second.post(f"/api/runs/{other.json()['id']}/stop")
            release.set()
            stopped = wait_run(first, run.json()['id'])
            if other is not None and other.status_code == 201:
                wait_run(second, other.json()['id'])
        assert stopped['status'] == 'stopped'
        assert not (source / 'late.txt').exists()
        assert (source / 'keep.txt').read_text() == 'user bytes'


def test_legacy_stop_reaps_real_pytest_descendant_before_source_release(tmp_path, model_server, monkeypatch):
    from flycode.orchestration.sources import Sources
    from flycode.orchestration.store import Store, default_state_path
    from flycode.verification.custody import stopped

    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    source = tmp_path / 'project'
    source.mkdir()
    marker = tmp_path / 'group.json'
    (source / 'test_owned.py').write_text(
        'import json, os, subprocess, sys, time\nfrom pathlib import Path\n'
        'def test_owned():\n'
        '    child = subprocess.Popen([sys.executable, "-c", '
        + repr('import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
               'print("ready",flush=True); time.sleep(60)') + '], stdout=subprocess.PIPE)\n'
        '    assert child.stdout.readline().strip() == b"ready"\n'
        f'    Path({str(marker)!r}).write_text(json.dumps({{"group": os.getpgrp()}}))\n'
        '    time.sleep(60)\n')
    with TestClient(create_app(tmp_path, tmp_path / 'history', tmp_path / 'catalogue'),
                    base_url='http://localhost') as client:
        client.headers['X-FlyCode-Token'] = client.get('/api/bootstrap').json()['token']
        response = client.post('/api/runs', json={'task': 'Run the existing tests.', 'project': 'project',
            'model': 'reasoner', 'decision_model': None, 'max_steps': 5})
        assert response.status_code == 201, response.text
        identity = response.json()['id']
        try:
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert marker.exists(), client.get(f'/api/runs/{identity}').json()
            start = time.monotonic()
            assert client.post(f'/api/runs/{identity}/stop').status_code == 200
            assert time.monotonic() - start < .5
            record = wait_run(client, identity)
            assert record['status'] == 'stopped', record
            store = Store(default_state_path())
            try:
                claim = Sources(store).get(record['source_claim_id'])
                assert claim.state == 'released' and claim.processes
                for ref in claim.processes:
                    assert ref.version == 'tool-custody-v1'
                    with stopped(ref) as receipt:
                        assert receipt.stop_confirmed
                assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
            finally:
                store.close()
            group = json.loads(marker.read_text())['group']
            assert group != os.getpgrp()
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError('The admitted pytest group still exists after source release')
        finally:
            client.post(f'/api/runs/{identity}/stop')


async def test_legacy_observation_has_bound_process_custody(tmp_path):
    from flycode.domain import TaskSpec
    from flycode.orchestration.sources import Sources
    from flycode.orchestration.store import Store, default_state_path
    from flycode.web.execution import LegacyEnvironment, SourceExecution

    source, directory = tmp_path / 'source', tmp_path / 'run'
    source.mkdir()
    directory.mkdir()
    (source / 'keep.txt').write_text('user bytes')
    execution = SourceExecution(source, directory, 'observation-owner')
    try:
        observation = await LegacyEnvironment(execution, seed=0, should_stop=lambda: False).reset(
            TaskSpec(id='observe', task='Inspect the existing folder.'))
        assert observation.file_tree == ['keep.txt']
        store = Store(default_state_path())
        try:
            assert Sources(store).get(execution.claim.id).processes, 'Git observations escaped process custody'
        finally:
            store.close()
    finally:
        execution.finish()


@pytest.mark.parametrize('old_status', ['running', 'interrupted'])
@pytest.mark.parametrize('hidden', [False, True])
def test_restart_quarantines_legacy_history_without_source_stop_proof(tmp_path, model_server, monkeypatch, old_status, hidden):
    from flycode.web.runs import RunRecord, RunRequest

    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'keep.txt').write_text('user bytes')
    history = tmp_path / 'history'
    old = RunRecord(id='c' * 32, request=RunRequest(task='Old unfinished run.', project='project'), status=old_status)
    directory = history / old.id
    directory.mkdir(parents=True)
    (directory / 'record.json').write_text(old.model_dump_json())
    if hidden:
        os.utime(directory / 'record.json', (1, 1))
        for number in range(50):
            terminal = RunRecord(id=f'{number:032x}', request=RunRequest(task='Historical completed run.'),
                                 status='completed')
            location = history / terminal.id
            location.mkdir()
            (location / 'record.json').write_text(terminal.model_dump_json())
    with TestClient(create_app(tmp_path, history, tmp_path / 'catalogue'), base_url='http://localhost') as client:
        client.headers['X-FlyCode-Token'] = client.get('/api/bootstrap').json()['token']
        response = client.post('/api/runs', json={'task': 'New writer.', 'project': 'project',
            'model': 'reasoner', 'decision_model': None, 'max_steps': 1})
        assert response.status_code == 409, response.text
        assert not model_server.requests and not model_server.get_requests
        assert (source / 'keep.txt').read_text() == 'user bytes'


def test_failed_thread_start_releases_never_started_source_and_preserves_failure(tmp_path, monkeypatch):
    from threading import Thread

    from flycode.orchestration.sources import Sources
    from flycode.orchestration.store import Store, default_state_path
    from flycode.web import runs

    class RefusedThread(Thread):
        def start(self):
            raise RuntimeError('Owned thread was never started')

    manager = runs.RunManager(tmp_path, tmp_path / 'history', tmp_path / 'catalogue')
    monkeypatch.setattr(runs, 'Thread', RefusedThread)
    with pytest.raises(RuntimeError, match='never started'):
        manager.start(runs.RunRequest(task='Fix the demo.', llm='heuristic'))
    record = manager.history()[0]
    assert record['status'] == 'failed' and 'never started' in record['error']
    store = Store(default_state_path())
    try:
        assert Sources(store).get(record['source_claim_id']).state == 'released'
    finally:
        store.close()
    manager.close()


def test_actual_legacy_host_crash_retains_source_until_owned_pytest_group_stops(tmp_path, model_server, monkeypatch):
    from flycode.orchestration.sources import Sources
    from flycode.orchestration.store import Store, StoreError, default_state_path
    from flycode.web import runs

    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    source = tmp_path / 'project'
    source.mkdir()
    marker, run_file = tmp_path / 'group.json', tmp_path / 'run-id'
    (source / 'test_owned.py').write_text(
        'import json,os,subprocess,sys,time\nfrom pathlib import Path\n'
        'def test_owned():\n'
        '    child=subprocess.Popen([sys.executable,"-c",'
        + repr('import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
               'print("ready",flush=True); time.sleep(60)') + '],stdout=subprocess.PIPE)\n'
        '    assert child.stdout.readline().strip()==b"ready"\n'
        f'    Path({str(marker)!r}).write_text(json.dumps({{"group":os.getpgrp()}}))\n'
        '    time.sleep(60)\n')
    command = ('import time\nfrom pathlib import Path\nfrom flycode.web.runs import RunManager,RunRequest\n'
        f'm=RunManager(Path({str(tmp_path)!r}),Path({str(tmp_path / "history")!r}),Path({str(tmp_path / "catalogue")!r}))\n'
        'r=m.start(RunRequest(task="Run the existing tests.",project="project",model="reasoner",decision_model=None,max_steps=5))\n'
        f'Path({str(run_file)!r}).write_text(r["id"])\ntime.sleep(60)\n')
    environment = {**os.environ, 'PYTHONPATH': str(Path(runs.__file__).resolve().parents[2])}
    with (tmp_path / 'host.log').open('wb') as stream:
        coordinator = subprocess.Popen([sys.executable, '-c', command], env=environment,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                assert coordinator.poll() is None, (tmp_path / 'host.log').read_text()
                time.sleep(.02)
            assert marker.exists(), (tmp_path / 'host.log').read_text()
            coordinator.kill()
            coordinator.wait(timeout=5)
            record = json.loads((tmp_path / 'history' / run_file.read_text() / 'record.json').read_text())
            store = Store(default_state_path())
            try:
                sources = Sources(store)
                with pytest.raises(StoreError, match='process_unconfirmed'):
                    sources.settle(record['source_claim_id'])
                deadline = time.monotonic() + 12
                while True:
                    try:
                        settled = sources.settle(record['source_claim_id'])
                        break
                    except StoreError as error:
                        assert error.code == 'process_unconfirmed' and time.monotonic() < deadline
                        time.sleep(.05)
                assert settled.state == 'released' and len(settled.processes) >= 2
                assert store.db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
            finally:
                store.close()
            group = json.loads(marker.read_text())['group']
            assert group != os.getpgrp()
            with pytest.raises(ProcessLookupError):
                os.killpg(group, 0)
        finally:
            if coordinator.poll() is None:
                coordinator.kill()
            coordinator.wait(timeout=5)
