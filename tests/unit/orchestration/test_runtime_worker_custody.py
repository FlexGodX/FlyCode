"""Native REDs for durable canonical worker custody; implementation is pending."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flycode.orchestration.runtime import Runtime
from flycode.orchestration.store import StoreError
from flycode.orchestration.tools import Tools

COORDINATOR = '''
import json, subprocess, sys, time
from pathlib import Path
from flycode.domain.orchestration import AccessScope
from flycode.llm.catalogue import ModelCatalogue, ModelDescriptor
from flycode.orchestration import runtime as module
from flycode.orchestration.store import Store

config = json.loads(Path(sys.argv[1]).read_text())
catalogue = ModelCatalogue(config['model_url'])
catalogue.entries = {key: ModelDescriptor.model_validate(value) for key, value in config['models'].items()}
store = Store(Path(config['database']), catalogue=catalogue)
scope = AccessScope.model_validate(config['scope'])
runtime = module.Runtime(store, Path(config['runtime']))
spawned = []
original_popen = subprocess.Popen

def actual_spawn(argv, *args, **kwargs):
    target = '-m' in argv and 'flycode.verification.custody' in argv
    before = runtime.worker(scope, config['attempt_id']).model_dump(mode='json') if target else None
    process = original_popen(argv, *args, **kwargs)
    if target:
        spawned.append((process, before))
    return process

subprocess.Popen = actual_spawn

def record_started():
    assert len(spawned) == 1, 'Expected one actual canonical worker Popen'
    process, before = spawned[0]
    record = runtime.worker(scope, config['attempt_id'])
    assert record.process is not None and record.pid != process.pid and process.poll() is None
    receipt = json.loads((Path(record.process.directory) / 'process.json').read_text())
    launch = json.loads((Path(record.process.directory) / 'process-launch.json').read_text())
    assert receipt['ref'] == record.process.model_dump(mode='json') and receipt['started']
    assert receipt['pid'] == record.pid
    data = {'worker': record.model_dump(mode='json'), 'before_spawn': before, 'actual_pid': record.pid,
            'actual_argv': launch['request']['argv'], 'actual_poll': None, 'guardian_pid': process.pid}
    marker = Path(config['marker'])
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps(data)); temporary.replace(marker)

if config['gate'] is True:
    send = module.wire.send
    def before_config(channel, payload):
        if 'database' in payload and 'attempt_id' in payload:
            record_started()
            while True: time.sleep(.02)
        return send(channel, payload)
    module.wire.send = before_config

if config['gate'] == 'before_go':
    launch_worker = module.custody.launch
    def before_go(*args, **kwargs):
        values = list(args)
        admit = values[9]
        def hold(ref):
            admit(ref)
            process, before = spawned[0]
            record = runtime.worker(scope, config['attempt_id'])
            receipt = json.loads((Path(ref.directory) / 'process.json').read_text())
            actual = json.loads((Path(ref.directory) / 'process-launch.json').read_text())
            assert process.poll() is None and receipt['state'] == 'admitted' and not receipt['started']
            marker = Path(config['marker'])
            temporary = marker.with_suffix('.tmp')
            temporary.write_text(json.dumps({'worker': record.model_dump(mode='json'), 'before_spawn': before,
                'actual_pid': None, 'actual_argv': actual['request']['argv'], 'actual_poll': None,
                'guardian_pid': process.pid, 'before_go': True}))
            temporary.replace(marker)
            while True: time.sleep(.02)
        values[9] = hold
        return launch_worker(*values, **kwargs)
    module.custody.launch = before_go

handle = runtime.launch(scope, config['attempt_id'], config['fence'], Path(config['source']), brain='heuristic_fsm')
record_started()
while True: time.sleep(.02)
'''


def wait_for(predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Owned fixture did not reach its declared native boundary'
        time.sleep(.02)


def group_exists(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Permission failure never proves group absence.


@pytest.fixture
def coordinator(owned, tmp_path, request, model_server):
    store, _, host, _, _, attempt, source = owned
    marker = tmp_path / 'actual-worker.json'
    if not getattr(request, 'param', True):
        (source / 'tests/test_example.py').write_text(
            'import json, os, subprocess, sys, time\nfrom pathlib import Path\n'
            'def test_example():\n'
            '    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\n'
            '    Path("child.json").write_text(json.dumps({"pid": child.pid, "group": os.getpgrp()}))\n'
            '    time.sleep(30)\n')
        model_server.respond = lambda request: model_server.completion('{"candidates":[{"tool":"run_tests"}]}')
    config = {'database': str(store.path), 'model_url': store.catalogue.base_url,
        'models': {key: value.model_dump(mode='json') for key, value in store.catalogue.entries.items()},
        'scope': host.model_dump(mode='json'), 'runtime': str(tmp_path / 'runtime'),
        'attempt_id': attempt.id, 'fence': attempt.fence, 'source': str(source),
        'marker': str(marker), 'gate': getattr(request, 'param', True)}
    job, script = tmp_path / 'coordinator.json', tmp_path / 'coordinator.py'
    job.write_text(json.dumps(config))
    script.write_text(COORDINATOR)
    errors = (tmp_path / 'coordinator-errors.txt').open('wb')
    process = subprocess.Popen([sys.executable, str(script), str(job)], start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
    observed = None
    try:
        wait_for(lambda: marker.exists() or process.poll() is not None)
        assert marker.exists(), (tmp_path / 'coordinator-errors.txt').read_text()
        observed = json.loads(marker.read_text())
        assert process.poll() is None and observed['actual_poll'] is None
        assert 'flycode.orchestration.worker' in observed['actual_argv']
        current = Runtime(store, Path(config['runtime'])).worker(host, attempt.id)
        assert current.id == observed['worker']['id'] and current.pid == observed['actual_pid']
        assert current.attempt_id == attempt.id and current.fence == attempt.fence
        yield process, observed, Path(config['runtime'])
    finally:
        # Only this exact Popen object is signalled. Worker/descendant IDs are
        # observations, never signal authority; their existing watchdog handles EOF.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        errors.close()
        if observed and observed['actual_pid'] is not None:
            wait_for(lambda: not group_exists(observed['actual_pid']), timeout=12)


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_actual_worker_has_durable_exact_process_binding_before_agent_ready(owned, coordinator):
    store, owner, _, workflow, _, attempt, _ = owned
    _, observed, _ = coordinator
    record = observed['worker']
    before = observed['before_spawn']
    assert before['id'] == record['id'] and before['attempt_id'] == attempt.id and before['fence'] == attempt.fence
    assert before.get('process') is not None, 'Actual canonical worker spawned before durable exact ProcessRef binding'
    assert record.get('process') == before['process']
    assert record['process']['version'] == 'worker-custody-v1'
    receipt = json.loads((Path(record['process']['directory']) / 'process.json').read_text())
    assert receipt['ref'] == record['process'] and receipt['started']
    assert receipt['state'] == 'admitted' and not receipt['stop_confirmed']
    assert not store._attempt(attempt.id).stop_confirmed
    assert store.budget(owner, workflow.id).spent.model_calls == 0


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_live_worker_without_agent_calls_cannot_release_reservation(owned, coordinator):
    store, owner, host, workflow, task, attempt, _ = owned
    before = store.budget(owner, workflow.id)
    assert not store.db.execute('SELECT 1 FROM model_calls').fetchone()
    assert not store.db.execute('SELECT 1 FROM tool_calls').fetchone()
    with pytest.raises(StoreError, match='reconcile_required|process_unconfirmed'):
        store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    assert not store._attempt(attempt.id).stop_confirmed
    assert store.task(owner, task.id).current_attempt_id == attempt.id
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('coordinator', [False], indirect=True)
def test_coordinator_crash_retains_exact_stop_proof_and_unknown_tool_spending(owned, coordinator, tmp_path):
    store, owner, host, workflow, task, attempt, _ = owned
    process, observed, runtime_root = coordinator
    runtime = Runtime(store, runtime_root)
    repository = runtime.workspaces.repository(owner, attempt.id)
    marker = repository / 'child.json'
    wait_for(marker.exists)
    child = json.loads(marker.read_text())
    assert child['group'] == observed['actual_pid'] and os.getpgid(child['pid']) == child['group']
    call = next(call for call in Tools(store).calls(owner, workflow.id) if call.tool == 'run_tests')
    assert call.status == 'running' and call.attempt_id == attempt.id
    before = store.budget(owner, workflow.id)
    process.kill()
    process.wait(timeout=5)
    wait_for(lambda: not group_exists(observed['actual_pid']), timeout=12)
    store.recover(host)
    assert Tools(store).get(owner, call.id).status == 'uncertain'
    after = store.budget(owner, workflow.id)
    assert after.spent == before.spent and after.reserved == before.reserved and after.uncertain
    with pytest.raises(StoreError, match='resource_busy|reconcile_required'):
        store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    assert store.task(owner, task.id).current_attempt_id == attempt.id
    assert not store._attempt(attempt.id).stop_confirmed
    record = runtime.worker(owner, attempt.id).model_dump(mode='json')
    assert record.get('process') is not None, 'Watchdog stopped the owned group but no durable exact stop proof exists'
    from flycode.domain.verification import ProcessRef
    from flycode.verification.custody import stopped
    with stopped(ProcessRef.model_validate(record['process'])) as receipt:
        assert receipt.started and receipt.stop_confirmed and receipt.reason == 'coordinator_lost'
    assert runtime.reconcile(host, attempt.id).stop_confirmed
    assert Tools(store).get(owner, call.id).status == 'uncertain'
    assert not store._attempt(attempt.id).stop_confirmed
    assert store.budget(owner, workflow.id).spent == before.spent
    assert store.budget(owner, workflow.id).reserved == before.reserved


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('coordinator', ['before_go'], indirect=True)
def test_real_coordinator_death_before_go_has_exact_never_started_stop_proof(owned, coordinator):
    from flycode.domain.verification import ProcessRef
    from flycode.verification.custody import stopped

    store, owner, host, workflow, _, attempt, source = owned
    process, observed, runtime_root = coordinator
    ref = ProcessRef.model_validate(observed['worker']['process'])
    assert observed['before_spawn']['process'] == observed['worker']['process']
    assert observed['before_go'] and observed['actual_pid'] is None
    live = json.loads((Path(ref.directory) / 'process.json').read_text())
    assert live['ref'] == ref.model_dump(mode='json') and live['state'] == 'admitted' and not live['started']
    with pytest.raises(BlockingIOError), stopped(ref):
        pytest.fail('Guardian lock was released before GO')
    process.kill()
    process.wait(timeout=5)
    wait_for(lambda: json.loads((Path(ref.directory) / 'process.json').read_text())['state'] == 'stopped')
    with stopped(ref) as receipt:
        assert receipt.stop_confirmed and not receipt.started and receipt.reason == 'coordinator_lost'
    # Losing a physical source folder must not prevent exact local Stop proof.
    source.rename(source.with_name('source-relocated'))
    store.recover(host)
    record = Runtime(store, runtime_root).reconcile(host, attempt.id)
    assert record.stop_confirmed and record.pid is None
    assert store._attempt(attempt.id).stop_confirmed
    assert store.budget(owner, workflow.id).reserved.model_calls == 0
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    assert store.workflow(owner, workflow.id).started_at is None


def test_custody_forwards_hashed_duplex_fds_and_closes_all_child_ends(tmp_path):
    import socket

    from flycode.orchestration import wire
    from flycode.verification import custody

    rpc, child_rpc = socket.socketpair()
    control, child_control = socket.socketpair()
    output = tmp_path / 'custody'
    output.mkdir()
    script = ('import socket,sys,time\nfrom flycode.orchestration import wire\n'
        'rpc=socket.socket(fileno=int(sys.argv[1])); control=socket.socket(fileno=int(sys.argv[2]))\n'
        'wire.send(rpc,{"echo":wire.receive(rpc)}); control.sendall(control.recv(1)); time.sleep(30)\n')
    prepared = []
    try:
        with (output / 'stdout.txt').open('wb') as stdout, (output / 'stderr.txt').open('wb') as stderr:
            owned = custody.launch([sys.executable, '-c', script, str(child_rpc.fileno()), str(child_control.fileno())],
                tmp_path, {'PATH': os.environ.get('PATH', os.defpath),
                           'PYTHONPATH': str(Path(wire.__file__).resolve().parents[2])},
                stdout, stderr, output, 20, lambda: False, lambda state: None, lambda ref: None,
                on_prepare=prepared.append, version='worker-custody-v1',
                pass_fds=(child_rpc.fileno(), child_control.fileno()))
        child_rpc.close(); child_control.close()
        assert len(prepared) == 1 and owned.ref == prepared[0] and owned.pid != owned.supervisor.pid
        manifest = json.loads((output / 'process-launch.json').read_text())
        assert len(manifest['request']['pass_fds']) == 2
        rpc.settimeout(5); control.settimeout(5)
        wire.send(rpc, {'payload': 'Actual duplex RPC'})
        assert wire.receive(rpc) == {'echo': {'payload': 'Actual duplex RPC'}}
        control.sendall(b'X'); assert control.recv(1) == b'X'
        owned.cancel()
        receipt = owned.finish()
        assert receipt.started and receipt.stop_confirmed and receipt.reason == 'cancelled'
        assert receipt.pid == owned.pid
        assert rpc.recv(1) == b'' and control.recv(1) == b''
    finally:
        if 'owned' in locals() and owned.receipt is None:
            owned.cancel(); owned.finish()
        for channel in (rpc, child_rpc, control, child_control):
            channel.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('damage', ['lock', 'receipt', 'missing_receipt'])
def test_replaced_custody_objects_cannot_confirm_worker_stop(owned, tmp_path, monkeypatch, damage):
    from threading import current_thread

    from flycode.verification import custody

    store, owner, host, workflow, _, attempt, source = owned
    finish = custody.Launch.finish
    damaged = []

    def corrupt_before_validation(launched):
        if current_thread().name == 'flycode-worker-supervisor' and not damaged:
            assert launched.supervisor.poll() is not None
            root = Path(launched.ref.directory)
            receipt = json.loads((root / 'process.json').read_text())
            assert receipt['ref'] == launched.ref.model_dump(mode='json')
            assert receipt['started'] and receipt['stop_confirmed'] and receipt['pid'] == launched.pid
            if damage == 'lock':
                (root / 'process.lock').rename(root / 'original.lock')
                (root / 'process.lock').write_text('replacement ownership object')
            elif damage == 'receipt':
                receipt['ref']['id'] = 'f' * 32
                (root / 'process.json').write_text(json.dumps(receipt))
            else:
                (root / 'process.json').unlink()
            damaged.append(launched.ref)
        return finish(launched)

    monkeypatch.setattr(custody.Launch, 'finish', corrupt_before_validation)
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(10), handle.error
        assert damaged and not handle.process_stopped.is_set()
        assert not runtime.worker(owner, attempt.id).stop_confirmed
        before = store.budget(owner, workflow.id)
        with pytest.raises(StoreError, match='process_unconfirmed'):
            runtime.reconcile(host, attempt.id)
        assert store.budget(owner, workflow.id) == before
        assert not store._attempt(attempt.id).stop_confirmed
        assert not store.db.execute('SELECT 1 FROM artifacts').fetchone()
    finally:
        handle.close()


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_guardian_crash_holds_unknown_http_and_never_uses_pid_as_stop_proof(owned, tmp_path, model_server):
    from threading import Event

    from flycode.orchestration.resources import Resources

    store, owner, host, workflow, _, attempt, source = owned
    entered, release = Event(), Event()
    def blocked(request):
        entered.set()
        assert release.wait(20)
        return model_server.completion('{"candidates":[{"tool":"read_file","arguments":{"path":"src/example.py"}}]}')
    model_server.respond = blocked
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(10)
        assert handle.process.pid != handle.guardian.pid and handle.process.poll() is None
        ref = runtime.worker(owner, attempt.id).process
        live = json.loads((Path(ref.directory) / 'process.json').read_text())
        assert live['started'] and live['pid'] == handle.process.pid and live['ref'] == ref.model_dump(mode='json')
        handle.guardian.kill()  # This exact Popen belongs to this test's Runtime.
        handle.guardian.wait(timeout=5)
        assert handle.finished.wait(8), handle.error
        wait_for(lambda: not group_exists(handle.process.pid), timeout=8)
        assert not handle.process_stopped.is_set() and not runtime.worker(owner, attempt.id).stop_confirmed
        calls = Resources(store).calls(owner, workflow.id)
        assert len(calls) == 1 and calls[0].status == 'uncertain'
        budget = store.budget(owner, workflow.id)
        assert budget.uncertain and budget.spent.model_calls == 1
        with pytest.raises(StoreError, match='process_unconfirmed'):
            runtime.reconcile(host, attempt.id)
        assert store.budget(owner, workflow.id) == budget
    finally:
        release.set()
        handle.close()
        assert handle.rpc_done.wait(5)
    assert Resources(store).calls(owner, workflow.id)[0].usage.total_tokens == 11
    assert not store._attempt(attempt.id).stop_confirmed


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
@pytest.mark.parametrize('phase', ['stderr', 'rpc', 'supervisor'])
def test_partial_runtime_startup_stops_actual_worker_and_guardian(owned, tmp_path, monkeypatch, phase):
    from flycode.orchestration import runtime as module
    from flycode.verification import custody

    store, owner, host, workflow, _, attempt, source = owned
    actual = []
    launch, start = custody.launch, module.Thread.start

    def capture_launch(*args, **kwargs):
        launched = launch(*args, **kwargs)
        assert launched.pid != launched.supervisor.pid and launched.supervisor.poll() is None
        actual.append(launched)
        return launched

    def fail_thread(thread):
        if thread.name == 'flycode-worker-' + phase:
            raise RuntimeError('Injected owned startup phase ' + phase)
        return start(thread)

    monkeypatch.setattr(custody, 'launch', capture_launch)
    monkeypatch.setattr(module.Thread, 'start', fail_thread)
    runtime = Runtime(store, tmp_path / 'runtime')
    with pytest.raises(RuntimeError, match='Injected owned startup phase'):
        runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    assert len(actual) == 1
    with custody.stopped(actual[0].ref) as receipt:
        assert receipt.started and receipt.stop_confirmed and receipt.pid == actual[0].pid
    assert actual[0].supervisor.poll() is not None
    assert not group_exists(actual[0].pid)
    assert runtime.worker(owner, attempt.id).stop_confirmed and store._attempt(attempt.id).stop_confirmed
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    assert store.budget(owner, workflow.id).reserved.model_calls == 0


def test_guardian_death_before_go_never_promotes_started_false_to_stop_proof(tmp_path, monkeypatch):
    from flycode.verification import custody

    output = tmp_path / 'custody'
    output.mkdir()
    captured, prepared = [], []
    popen = custody.subprocess.Popen

    def actual_guardian(*args, **kwargs):
        process = popen(*args, **kwargs)
        if 'flycode.verification.custody' in args[0]:
            captured.append(process)
        return process

    def kill_guardian_before_go(ref):
        assert prepared == [ref] and len(captured) == 1
        receipt = json.loads((output / 'process.json').read_text())
        assert receipt['ref'] == ref.model_dump(mode='json') and receipt['state'] == 'admitted'
        assert not receipt['started'] and not receipt['stop_confirmed']
        captured[0].kill()  # Exact owned Popen, not a persisted/inferred PID.
        captured[0].wait(timeout=5)

    monkeypatch.setattr(custody.subprocess, 'Popen', actual_guardian)
    with (
        (output / 'stdout.txt').open('wb') as stdout,
        (output / 'stderr.txt').open('wb') as stderr,
        pytest.raises((EOFError, OSError)),
    ):
        custody.launch([sys.executable, '-c', 'raise AssertionError("GO must never run")'], tmp_path,
            {'PATH': os.environ.get('PATH', os.defpath)}, stdout, stderr, output, 10,
            lambda: False, lambda state: None, kill_guardian_before_go,
            on_prepare=prepared.append, version='worker-custody-v1')
    assert captured and captured[0].returncode is not None
    raw = json.loads((output / 'process.json').read_text())
    assert raw['state'] == 'admitted' and not raw['started'] and not raw['stop_confirmed']
    with pytest.raises(ValueError), custody.stopped(prepared[0]):
        pytest.fail('Free lock and started=False were incorrectly promoted to stopped proof')


def test_custody_socket_allocation_failure_closes_exact_owned_lock_fd(tmp_path, monkeypatch):
    from flycode.verification import custody

    output = tmp_path / 'custody'
    output.mkdir()
    opened = []
    original = custody.os.open

    def actual_open(path, *args, **kwargs):
        descriptor = original(path, *args, **kwargs)
        if Path(path) == output / 'process.lock':
            opened.append(descriptor)
        return descriptor

    def fail_pair():
        raise OSError('Injected custody socket allocation failure')

    monkeypatch.setattr(custody.os, 'open', actual_open)
    monkeypatch.setattr(custody.socket, 'socketpair', fail_pair)
    with (
        (output / 'stdout.txt').open('wb') as stdout,
        (output / 'stderr.txt').open('wb') as stderr,
        pytest.raises(OSError, match='Injected custody socket allocation failure'),
    ):
        custody.launch([sys.executable, '-c', 'pass'], tmp_path, {}, stdout, stderr, output, 10,
            lambda: False, lambda state: None, lambda ref: None)
    assert len(opened) == 1
    try:
        with pytest.raises(OSError, match='Bad file descriptor'):
            os.fstat(opened[0])
    finally:
        try:
            os.close(opened[0])
        except OSError:
            pass


@pytest.mark.parametrize('owned', [{'assigned': True}], indirect=True)
def test_exact_stopped_worker_keeps_inflight_http_reserved_until_actual_late_usage(owned, tmp_path, model_server):
    from threading import Event

    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.resources import Resources
    from flycode.verification import custody

    store, owner, host, workflow, task, attempt, source = owned
    entered, release = Event(), Event()

    def blocked(request):
        entered.set()
        assert release.wait(20)
        return model_server.completion('{"candidates":[{"tool":"read_file","arguments":{"path":"src/example.py"}}]}')

    model_server.respond = blocked
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert entered.wait(10)
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-inflight'))
        assert handle.process_stopped.wait(3)
        ref = runtime.worker(owner, attempt.id).process
        with custody.stopped(ref) as receipt:
            assert receipt.started and receipt.stop_confirmed and receipt.pid == handle.process.pid
        before = store.budget(owner, workflow.id)
        assert runtime.reconcile(host, attempt.id).stop_confirmed
        after = store.budget(owner, workflow.id)
        assert after.spent == before.spent and after.reserved == before.reserved and after.uncertain
        assert Resources(store).calls(owner, workflow.id)[0].status == 'uncertain'
        assert not store._attempt(attempt.id).stop_confirmed
        with pytest.raises(StoreError, match='resource_busy'):
            store.confirm_stopped(host, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    finally:
        release.set()
        handle.close()
        assert handle.finished.wait(5), handle.error
    call = Resources(store).calls(owner, workflow.id)[0]
    assert call.late and call.usage.total_tokens == 11
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store.budget(owner, workflow.id).reserved.model_calls == 0
