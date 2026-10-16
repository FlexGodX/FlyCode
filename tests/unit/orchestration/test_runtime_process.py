"""Real worker processes keep model transport separate from stop supervision."""

import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from flycode.domain.orchestration import TaskCommand
from flycode.orchestration.resources import Resources
from flycode.orchestration.store import StoreError
from flycode.orchestration.tools import Tools


def test_local_verification_candidates_get_distinct_execution_ids(owned):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, host, workflow, _, attempt, _ = owned
    cortex = AttemptCortex(store, host, attempt.id, attempt.fence)
    context = CortexContext(task='Verify.', behavior_mode='verify', observation=Observation(task='Verify'))
    first, second = cortex.generate_candidates(context), cortex.generate_candidates(context)
    assert first[0].tool == second[0].tool == 'run_tests'
    assert first[0].id != second[0].id
    assert store.budget(owner, workflow.id).spent.model_calls == 0


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_real_worker_runs_agent_in_private_copy_without_claiming_completion(owned, tmp_path):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, task, attempt, source = owned
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(20)
        assert handle.process_stopped.is_set()
        record = runtime.worker(owner, attempt.id)
        assert record.stop_confirmed and record.result_hash
        assert record.pid == handle.process.pid and record.exit_code is not None
        assert (source / 'src/example.py').read_text() == 'answer = 0\n'
        assert store.task(owner, task.id).state != 'succeeded'
        assert store._attempt(attempt.id).state == 'awaiting_review'
        from flycode.orchestration.artifacts import Artifacts
        assert Artifacts(store, runtime.root).for_attempt(owner, attempt.id).result_hash == record.result_hash
        assert Resources(store).calls(owner, workflow.id)[0].status == 'completed'
        assert any(call.tool == 'read_file' and call.status == 'completed' for call in Tools(store).calls(owner, workflow.id))
        events = store.events(owner, workflow.id)
        assert any(event.kind == 'attempt_started' for event in events)
        assert any(event.kind == 'agent_activity' for event in events)
        assert any(event.kind == 'worker_stopped' for event in events)
    finally:
        handle.close()


def test_terminal_worker_control_close_does_not_revoke_accepted_result(owned, tmp_path, monkeypatch):
    from flycode.orchestration import runtime as module

    store, owner, host, _, task, attempt, source = owned
    runtime = module.Runtime(store, tmp_path / 'runtime')
    release, injected = Event(), Event()
    send = module.wire.send
    handle = None

    def hold_result_reply(channel, payload):
        if handle is not None and handle.result is not None and channel is handle.rpc:
            assert release.wait(5)
        return send(channel, payload)

    monkeypatch.setattr(module.wire, 'send', hold_result_reply)
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    control = handle.control

    class TerminalControl:
        def sendall(self, data):
            if handle.result is not None:
                release.set()
                assert handle.rpc_done.wait(5)
                injected.set()
                raise BrokenPipeError('Terminal worker closed its control channel')
            return control.sendall(data)

        def close(self):
            control.close()

    handle.control = TerminalControl()
    try:
        assert handle.finished.wait(10), handle.error
        assert injected.is_set() and handle.error is None
        assert runtime.worker(owner, attempt.id).stop_confirmed
        assert store.task(owner, task.id).state == 'awaiting_review'
    finally:
        release.set()
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_stop_kills_owned_worker_while_http_remains_inflight(owned, tmp_path, model_server):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, task, attempt, source = owned
    entered, release = Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(15)
        return model_server.completion('[]')
    model_server.respond = response
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(10)
        current = store.task(owner, task.id)
        started = time.monotonic()
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-inflight'))
        assert time.monotonic() - started < .5
        assert handle.process_stopped.wait(3)
        assert not handle.finished.is_set()
        assert store.task(owner, task.id).current_attempt_id == attempt.id
        assert Resources(store).calls(owner, workflow.id)[0].status == 'running'
        release.set()
        assert handle.finished.wait(5)
        assert store._attempt(attempt.id).stop_confirmed
        assert store.task(owner, task.id).state == 'cancelled'
        call = Resources(store).calls(owner, workflow.id)[0]
        assert call.status == 'completed' and call.late
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        assert store.budget(owner, workflow.id).spent.tool_calls == 0
    finally:
        release.set()
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_untrusted_profile_is_blocked_before_copy_or_process(owned, tmp_path):
    from flycode.orchestration.runtime import Runtime

    store, _, host, _, _, attempt, source = owned
    runtime = Runtime(store, tmp_path / 'runtime')
    with pytest.raises(StoreError, match='unsupported_sandbox'):
        runtime.launch(host, attempt.id, attempt.fence, source, trust='untrusted')
    assert not (tmp_path / 'runtime').exists()
    assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_stop_cleans_pytest_descendants_but_keeps_unknown_effect_for_reconciliation(owned, tmp_path, model_server):
    from flycode.orchestration.runtime import Runtime
    from flycode.verification.runner import _group_gone

    store, owner, host, workflow, task, attempt, source = owned
    (source / 'tests/test_example.py').write_text(
        'import subprocess, sys, time\nfrom pathlib import Path\n'
        'def test_example():\n'
        '    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\n'
        '    Path("child.pid").write_text(str(child.pid))\n'
        '    time.sleep(30)\n')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{'tool': 'run_tests'}]}))
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        copy = runtime.workspaces.repository(owner, attempt.id)
        deadline = time.monotonic() + 10
        while not (copy / 'child.pid').exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        child = int((copy / 'child.pid').read_text())
        assert os.getpgid(child) == handle.process.pid
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-pytest'))
        assert handle.process_stopped.wait(8)
        assert handle.finished.wait(3)
        assert _group_gone(handle.process.pid)
        call = Tools(store).calls(owner, workflow.id)[0]
        assert call.status == 'uncertain' and store.budget(owner, workflow.id).uncertain
        assert store.task(owner, task.id).current_attempt_id == attempt.id
        assert not store._attempt(attempt.id).stop_confirmed
        assert not (source / 'child.pid').exists()
    finally:
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_supervisor_renews_lease_independently_of_blocking_model(owned, tmp_path, model_server):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, task, attempt, source = owned
    entered, release = Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(20)
        return model_server.completion('[]')
    model_server.respond = response
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(10)
        initial = store._attempt(attempt.id).lease_until
        deadline = time.monotonic() + 13
        while store._attempt(attempt.id).lease_until <= initial:
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert not release.is_set() and handle.process.poll() is None
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=current.revision,
            idempotency_key='pause-blocked-model'))
        assert handle.process_stopped.wait(3)
        release.set()
        assert handle.finished.wait(5)
        assert store.task(owner, task.id).paused
        assert store.task(owner, task.id).state == 'retry_wait'
        assert store.budget(owner, workflow.id).spent.model_calls == 1
    finally:
        release.set()
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_recovery_marks_worker_uncertain_and_never_relaunches_old_session(owned, tmp_path, model_server):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, _, _, attempt, source = owned
    entered, release = Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(15)
        return model_server.completion('[]')
    model_server.respond = response
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(10)
        store.recover(host)
        assert runtime.worker(owner, attempt.id).state == 'uncertain'
        assert handle.process_stopped.wait(3)
        with pytest.raises(StoreError, match='fence'):
            runtime.launch(host, attempt.id, attempt.fence, source)
        release.set()
        assert handle.finished.wait(5)
    finally:
        release.set()
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_worker_control_eof_stops_group_while_rpc_handshake_is_blocked(owned, tmp_path):
    import flycode
    from flycode.domain import TaskSpec
    from flycode.domain.orchestration import WorkerConfig
    from flycode.orchestration import wire
    from flycode.orchestration.workspaces import Workspaces
    from flycode.verification.runner import _group_gone, _terminate

    store, _, host, _, task, attempt, source = owned
    workspaces = Workspaces(store, tmp_path / 'artifacts')
    record = workspaces.prepare(host, attempt.id, attempt.fence, source)
    directory = workspaces.root / record.id
    config = WorkerConfig(database=str(store.path), repository=str(directory / 'repository'), scope=host,
        attempt_id=attempt.id, fence=attempt.fence, task=TaskSpec(id=task.id, task=task.objective),
        brain='heuristic_fsm', seed=0)
    rpc, child_rpc = socket.socketpair()
    control, child_control = socket.socketpair()
    process = subprocess.Popen([sys.executable, '-m', 'flycode.orchestration.worker',
        str(child_rpc.fileno()), str(child_control.fileno())], cwd=directory,
        env={'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path),
             'PYTHONPATH': str(Path(flycode.__file__).parent.parent)},
        pass_fds=(child_rpc.fileno(), child_control.fileno()), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_rpc.close()
    child_control.close()
    try:
        rpc.settimeout(10)
        wire.send(rpc, config.model_dump(mode='json'))
        assert wire.receive(rpc)['operation'] == 'ready'
        control.close()
        assert process.wait(timeout=3) != 0
        assert _group_gone(process.pid)
        assert store._attempt(attempt.id).state == 'assigned'
    finally:
        control.close()
        rpc.close()
        _terminate(process)


def test_worker_frame_rejects_oversized_header_without_waiting_for_body():
    from flycode.orchestration import wire

    first, second = socket.socketpair()
    try:
        first.sendall(struct.pack('!I', wire.MAX_FRAME + 1))
        second.settimeout(.2)
        with pytest.raises(ValueError, match='length'):
            wire.receive(second)
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_missing_real_graph_is_a_startup_failure_without_fake_start_or_inference(owned, tmp_path, model_server):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, _, attempt, source = owned
    before = len(model_server.requests)
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='full')
    try:
        assert handle.finished.wait(10)
        record = runtime.worker(owner, attempt.id)
        assert record.stop_confirmed and record.error == 'worker:ValueError'
        assert store.workflow(owner, workflow.id).started_at is None
        assert not any(e.kind == 'attempt_started' for e in store.events(owner, workflow.id))
        assert store._attempt(attempt.id).state == 'failed'
        assert len(model_server.requests) == before
    finally:
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('output_size', [96, 262144])
def test_worker_failure_retains_bounded_stderr_without_start_or_spend(owned, tmp_path, monkeypatch, output_size):
    import hashlib

    from flycode.orchestration import runtime as module
    from flycode.orchestration.store import Store

    store, owner, host, workflow, _, attempt, source = owned
    popen = module.subprocess.Popen
    marker = b'Traceback: injected startup failure\nInjectedWorkerFailure: diagnostic marker\n'
    # Fault injection keeps the real process, initial wire handshake and host custody.
    script = ('import os,socket,sys\nfrom flycode.orchestration import wire\n'
        'wire.receive(socket.socket(fileno=int(sys.argv[1])))\n'
        f'data={marker!r}+b"x"*{output_size}\n'
        'while data:\n    data=data[os.write(2,data):]\n'
        'os._exit(1)\n')
    def failed_worker(argv, *args, **kwargs):
        if 'flycode.orchestration.worker' in argv:
            argv = [argv[0], '-c', script, *argv[-2:]]
        return popen(argv, *args, **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', failed_worker)
    runtime = module.Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(10), handle.error
        assert handle.process_stopped.is_set()
        record = runtime.worker(owner, attempt.id)
        assert record.stop_confirmed and record.result_hash is None
        assert store._attempt(attempt.id).state == 'failed'
        assert store.workflow(owner, workflow.id).started_at is None
        assert store.budget(owner, workflow.id).spent.model_calls == 0
        assert store.budget(owner, workflow.id).spent.tool_calls == 0
        assert (source / 'src/example.py').read_text() == 'answer = 0\n'
        connection = Store(store.path)
        try:
            stopped = next(event for event in connection.events(owner, workflow.id) if event.kind == 'worker_stopped')
        finally:
            connection.close()
        diagnostic = stopped.data['stderr']
        retained = (handle.directory / diagnostic['path']).read_bytes()
        assert retained.startswith(marker) and len(retained) <= 65536
        assert diagnostic['observed_bytes'] == len(marker) + output_size
        assert diagnostic['retained_bytes'] == len(retained)
        assert diagnostic['truncated'] == (output_size == 262144)
        assert diagnostic['sha256'] == hashlib.sha256(retained).hexdigest()
        assert 'diagnostic marker' not in stopped.model_dump_json()
    finally:
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_stop_remains_responsive_while_worker_floods_stderr(owned, tmp_path, monkeypatch):
    from flycode.orchestration import runtime as module

    store, owner, host, workflow, task, attempt, source = owned
    popen = module.subprocess.Popen
    script = ('import os,socket,sys,time\nfrom flycode.orchestration import wire\n'
        'wire.receive(socket.socket(fileno=int(sys.argv[1])))\n'
        'while True:\n    os.write(2,b"diagnostic overflow\\n"*512)\n    time.sleep(.001)\n')
    def noisy_worker(argv, *args, **kwargs):
        if 'flycode.orchestration.worker' in argv:
            argv = [argv[0], '-c', script, *argv[-2:]]
        return popen(argv, *args, **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', noisy_worker)
    runtime = module.Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        output = handle.directory / 'worker-stderr.log'
        deadline = time.monotonic() + 5
        while not output.exists() or output.stat().st_size < 65536:
            assert time.monotonic() < deadline
            time.sleep(.01)
        current = store.task(owner, task.id)
        started = time.monotonic()
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='stop-noisy-worker'))
        assert time.monotonic() - started < .5
        assert handle.process_stopped.wait(3) and handle.finished.wait(3)
        assert output.stat().st_size == 65536
        event = next(e for e in store.events(owner, workflow.id) if e.kind == 'worker_stopped')
        assert event.data['stderr']['truncated'] and event.data['stderr']['observed_bytes'] > 65536
        assert store.task(owner, task.id).state == 'cancelled'
        assert store.budget(owner, workflow.id).reserved.model_calls == 0
        assert store.budget(owner, workflow.id).spent.model_calls == 0
        assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    finally:
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_failed_process_launch_releases_only_the_proven_unstarted_attempt(owned, tmp_path, monkeypatch):
    from flycode.orchestration import runtime as module

    store, owner, host, workflow, _, attempt, source = owned
    runtime = module.Runtime(store, tmp_path / 'runtime')
    monkeypatch.setattr(module.sys, 'executable', str(tmp_path / 'missing-python'))
    with pytest.raises(FileNotFoundError):
        runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    record = runtime.worker(owner, attempt.id)
    assert record.stop_confirmed and record.pid is None
    assert record.state == 'exited' and record.error == 'FileNotFoundError'
    assert store._attempt(attempt.id).state == 'failed'
    assert store._attempt(attempt.id).stop_confirmed
    assert store.workflow(owner, workflow.id).started_at is None
    assert store.budget(owner, workflow.id).spent.model_calls == 0


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('failure', ['socket', 'home', 'stderr_thread', 'rpc_thread', 'supervisor_thread'])
def test_partial_launch_failure_closes_resources_without_start_or_spend(owned, tmp_path, monkeypatch, failure):
    from flycode.orchestration import runtime as module
    from flycode.verification.runner import _group_gone

    store, owner, host, workflow, _, attempt, source = owned
    runtime = module.Runtime(store, tmp_path / 'runtime')
    sockets, processes = [], []
    socketpair, mkdir, start, popen = socket.socketpair, Path.mkdir, module.Thread.start, module.subprocess.Popen
    def create_pair(*args, **kwargs):
        if failure == 'socket' and sockets:
            raise OSError('injected socket allocation failure')
        pair = socketpair(*args, **kwargs)
        sockets.extend(pair)
        return pair
    def create_home(path, *args, **kwargs):
        if failure == 'home' and path.name == 'home':
            raise PermissionError('injected home creation failure')
        return mkdir(path, *args, **kwargs)
    def start_thread(thread):
        target = getattr(thread, '_target', None)
        if thread.name == {'stderr_thread': 'flycode-worker-stderr', 'rpc_thread': 'flycode-worker-rpc',
                           'supervisor_thread': 'flycode-worker-supervisor'}.get(failure) or (
            getattr(target, '__name__', None) == {'stderr_thread': '_capture_stderr', 'rpc_thread': '_serve',
                                                'supervisor_thread': '_supervise'}.get(failure)):
            raise RuntimeError('injected thread creation failure')
        return start(thread)
    def create_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        if 'flycode.orchestration.worker' in args[0]:
            processes.append(process)
        return process
    monkeypatch.setattr(module.socket, 'socketpair', create_pair)
    monkeypatch.setattr(Path, 'mkdir', create_home)
    monkeypatch.setattr(module.Thread, 'start', start_thread)
    monkeypatch.setattr(module.subprocess, 'Popen', create_process)
    with pytest.raises((OSError, RuntimeError), match='injected'):
        runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    assert sockets and all(channel.fileno() == -1 for channel in sockets)
    assert all(process.poll() is not None and _group_gone(process.pid) for process in processes)
    record = runtime.worker(owner, attempt.id)
    assert record.state == 'exited' and record.stop_confirmed
    assert store._attempt(attempt.id).state == 'failed'
    assert store._attempt(attempt.id).stop_confirmed
    assert store.workflow(owner, workflow.id).started_at is None
    assert store.budget(owner, workflow.id).spent.model_calls == 0


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_client_cleanup_failure_still_finishes_supervision_and_retains_raw_result(owned, tmp_path, monkeypatch):
    from threading import current_thread

    from openai import OpenAI

    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, task, attempt, source = owned
    closed, close = Event(), OpenAI.close
    def failed_close(client):
        close(client)
        if current_thread().name == 'flycode-worker-rpc':
            closed.set()
            raise OSError('injected client cleanup failure')
    monkeypatch.setattr(OpenAI, 'close', failed_close)
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert closed.wait(10)
        assert handle.finished.wait(3)
        record = runtime.worker(owner, attempt.id)
        assert record.stop_confirmed and record.error == 'OSError:client_cleanup'
        assert record.result_hash and (handle.directory / 'run-result.json').exists()
        assert store.task(owner, task.id).state != 'succeeded'
        assert Resources(store).calls(owner, workflow.id)[0].status == 'completed'
    finally:
        handle.rpc_done.set()
        handle.close()
        assert handle.finished.wait(5)


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('thread_name', ['flycode-worker-rpc', 'flycode-worker-supervisor'])
def test_runtime_database_open_failure_stops_process_without_claiming_success(owned, tmp_path, monkeypatch, thread_name):
    import sqlite3
    from threading import current_thread

    from flycode.orchestration.runtime import Runtime, WorkerHandle
    from flycode.verification.runner import _group_gone

    store, owner, host, _, task, attempt, source = owned
    connect = WorkerHandle._connection
    def failed_connection(handle):
        if current_thread().name == thread_name:
            raise sqlite3.OperationalError('injected database open failure')
        return connect(handle)
    monkeypatch.setattr(WorkerHandle, '_connection', failed_connection)
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(10)
        assert handle.process_stopped.is_set() and _group_gone(handle.process.pid)
        assert handle.error.startswith('OperationalError:')
        assert store.task(owner, task.id).state != 'succeeded'
        if thread_name == 'flycode-worker-supervisor':
            assert store.task(owner, task.id).current_attempt_id == attempt.id
            assert not runtime.worker(owner, attempt.id).stop_confirmed
    finally:
        handle.close()
