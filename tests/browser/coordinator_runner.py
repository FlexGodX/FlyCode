"""Run real browser controls against owned application/coordinator processes."""

import argparse
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests'))

from conftest import model_server
from owner_runner import inventory

sys.path.insert(0, str(REPO / 'tests/unit/orchestration'))

from test_application_coordinator import (
    managed_pipeline,
    native_app,
    owned_service_control,
    until,
)

import flycode
from flycode.orchestration.host import Host
from flycode.orchestration.resources import Resources
from flycode.orchestration.runtime import Runtime
from flycode.orchestration.store import Store
from flycode.orchestration.tools import Tools
from flycode.orchestration.wire import receive, send
from flycode.verification.custody import stopped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('attach', 'lost-stop', 'runtime'), required=True)
    parser.add_argument('--stage', choices=('slow-review', 'slow-integration', 'model'), default='slow-review')
    parser.add_argument('--operation', choices=('pause', 'cancel'), default='pause')
    parser.add_argument('--root-loss', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.stage == 'model' and args.case != 'runtime':
        parser.error('The model stage requires runtime')
    root = args.output or Path(tempfile.mkdtemp(prefix='flycode-coordinator-browser-'))
    if args.stage == 'model':
        assert not root.resolve().is_relative_to(REPO)
    if os.environ.get('FLYCODE_EXPECTED_PACKAGE'):
        assert Path(flycode.__file__).is_relative_to(Path(os.environ['FLYCODE_EXPECTED_PACKAGE']))
    root.mkdir(parents=True, exist_ok=True)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    assert node, 'Set FLYCODE_NODE to an installed Node executable'
    report = {'case': args.case, 'stage': args.stage, 'operation': args.operation,
        'root_loss': args.root_loss, 'passed': False, 'phases': []}
    inputs, started = inventory(), time.monotonic()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    process = None
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_STATE_DATABASE', str(root / 'state.sqlite'))
            patch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(root / 'authority'))
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-catalogue'))
            patch.setenv('LMSTUDIO_BASE_URL', 'http://127.0.0.1:1/v1')
            pipeline = None
            if args.case == 'runtime':
                # Reuse the actual fixture generators; no production test hooks.
                server = stack.enter_context(contextmanager(model_server.__wrapped__)())
                pipeline = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(
                    root, server, patch, SimpleNamespace(param=args.stage)))
                store, owner, _, workflow, tasks, source, _, _, _, _, approve = pipeline
                patch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
                patch.setenv('LMSTUDIO_BASE_URL', server.url)
                approve()
                if args.stage == 'model':
                    entered, release = Event(), Event()
                    respond = server.respond
                    def hold(request):
                        entered.set()
                        assert release.wait(60), 'Owned model gate was not released'
                        return respond(request)
                    server.respond = hold
            else:
                source = root / 'source'
                source.mkdir()
                (source / 'owner.bin').write_bytes(b'\x00\xff preserve owner bytes\r\n')
                with Host(root / 'state.sqlite', root / 'host-state'):
                    pass
            daemon_control, inherited = stack.enter_context(owned_service_control())
            first_stack, second_stack = stack.enter_context(ExitStack()), stack.enter_context(ExitStack())
            first, first_http = first_stack.enter_context(native_app(source, root / 'history-a', control=inherited))
            inherited.close()
            _, second_http = second_stack.enter_context(native_app(source, root / 'history-b'))
            report['preparation_succeeded'] = True
            if pipeline and args.stage == 'model':
                # Release before either app or the owned daemon tears down.
                stack.callback(release.set)
            parent, child = socket.socketpair()
            stack.callback(parent.close)
            stack.callback(child.close)
            parent.settimeout(10)
            environment = {**os.environ, 'FLYCODE_UI_URL': str(first_http.base_url).rstrip('/'),
                'FLYCODE_SECOND_UI_URL': str(second_http.base_url).rstrip('/'),
                'FLYCODE_SCREENSHOTS': str(root / 'screenshots'), 'FLYCODE_BROWSER_CASE': args.case,
                'FLYCODE_PHASE_FD': str(child.fileno()), 'FLYCODE_STOP_OPERATION': args.operation,
                'FLYCODE_MODEL_STOP': '1' if args.stage == 'model' else '0',
                'FLYCODE_ROOT_LOSS': '1' if args.root_loss else '0'}
            if pipeline:
                target = tasks[1] if args.stage == 'model' else tasks[2] if args.stage == 'slow-review' else tasks[3]
                environment.update(FLYCODE_TASK_ID=target.id, FLYCODE_TASK_TITLE=target.title,
                    FLYCODE_TASK_SHORT_ID=target.short_id)
            with (root / 'browser.log').open('w') as output:
                process = subprocess.Popen([node, str(REPO / 'tests/browser/coordinator.cjs')],
                    env=environment, pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT)
                child.close()
                deadline = time.monotonic() + 150
                while process.poll() is None:
                    assert time.monotonic() < deadline, 'Owned browser observation deadline'
                    if not select.select([parent], [], [], .2)[0]:
                        continue
                    try:
                        message = receive(parent, max_frame=8192)
                    except EOFError:
                        process.wait(timeout=10)
                        break
                    phase = message['phase']
                    report['phases'].append(phase)
                    if phase == 'close-first':
                        identity = receive(daemon_control)
                        assert identity['pid'] == identity['sid'] and identity['pid'] != first.pid
                        first_stack.close()
                        assert first.returncode == 0
                        response = {'closed': True}
                    elif phase == 'ready' and pipeline:
                        if args.stage == 'model':
                            assert entered.wait(10) and not release.is_set()
                            calls = Resources(store).calls(owner, workflow.id)
                            assert len(calls) == 1 and calls[0].status == 'running'
                            model_call = calls[0]
                            attempt = store._attempt(model_call.attempt_id)
                            worker = Runtime(store, pipeline[7]).worker(owner, attempt.id)
                            assert worker.process is not None
                            report['held_model'] = {'call': model_call.model_dump(mode='json'),
                                'worker': worker.model_dump(mode='json'), 'attempt': attempt.model_dump(mode='json')}
                        else:
                            marker = root / ('review-started' if args.stage == 'slow-review' else 'integration-started')
                            until(marker.exists, timeout=45)
                        before_budget = store.budget(owner, workflow.id)
                        before_deadline = store.workflow(owner, workflow.id).deadline_at
                        before_calls = len(server.requests)
                        response = {'ready': True}
                    elif phase == 'replace-root':
                        original = source.with_name('displaced-source')
                        source.rename(original)
                        source.mkdir()
                        (source / 'replacement.txt').write_text('Replacement must stay untouched')
                        response = {'replaced': True}
                    elif phase == 'restart-second':
                        second_stack.close()
                        _, second_http = second_stack.enter_context(native_app(source, root / 'history-b'))
                        response = {'url': str(second_http.base_url).rstrip('/')}
                    elif phase == 'verify-stop' and pipeline:
                        if args.stage == 'model':
                            def worker_stopped(reference=worker.process):
                                try:
                                    with stopped(reference) as receipt:
                                        return receipt.started and receipt.stop_confirmed
                                except BlockingIOError:
                                    return False
                            until(worker_stopped, timeout=10)
                            assert not release.is_set() and Resources(store).get(owner, model_call.id).status == 'running'
                            assert store.task(owner, target.id).current_attempt_id == attempt.id
                            assert store.budget(owner, workflow.id) == before_budget
                            assert len(store.attempts(owner, workflow.id)) == 1 and len(server.requests) == before_calls
                            assert not Tools(store).calls(owner, workflow.id)
                            stop_commands = [dict(row) for row in store.db.execute(
                                "SELECT * FROM commands WHERE actor_id LIKE 'web:%' AND json_extract(result,'$.id')=?", (target.id,))]
                            assert len(stop_commands) == 1
                            stopped_task = store.task(owner, target.id)
                            report['stop'] = {'process_confirmed_before_http': True,
                                'budget': before_budget.model_dump(mode='json'), 'stop_commands': stop_commands,
                                'deadline_at': before_deadline}
                            send(parent, {'confirmed': True}, max_frame=8192)
                            continue
                        until(lambda: any(call.status == 'uncertain' for call in Tools(store).calls(owner, workflow.id)), timeout=10)
                        call = next(call for call in Tools(store).calls(owner, workflow.id) if call.status == 'uncertain')
                        assert call.process is not None
                        def confirmed(reference=call.process):
                            try:
                                with stopped(reference) as receipt:
                                    return receipt.started and receipt.stop_confirmed
                            except BlockingIOError:
                                return False
                        until(confirmed, timeout=10)
                        assert store.budget(owner, workflow.id).spent == before_budget.spent
                        assert store.budget(owner, workflow.id).reserved.tool_calls > 0
                        assert store.workflow(owner, workflow.id).deadline_at == before_deadline
                        assert len(server.requests) == before_calls
                        original = source.with_name('displaced-source') if args.root_loss else source
                        assert (original / 'src/parser.py').read_text() == 'value = 0\n'
                        assert (original / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
                        if args.root_loss:
                            assert (source / 'replacement.txt').read_text() == 'Replacement must stay untouched'
                        report['stop'] = {'process_confirmed': True, 'spent': before_budget.spent.model_dump(),
                            'deadline_at': before_deadline, 'unknown_tool_reservation_held': True,
                            'no_new_model_calls': True, 'owner_bytes_preserved': True}
                        response = {'confirmed': True}
                    elif phase == 'release-model' and pipeline and args.stage == 'model':
                        assert not release.is_set()
                        assert [dict(row) for row in store.db.execute(
                            "SELECT * FROM commands WHERE actor_id LIKE 'web:%' AND json_extract(result,'$.id')=?", (target.id,))] == stop_commands
                        assert store.task(owner, target.id) == stopped_task
                        assert store.budget(owner, workflow.id) == before_budget
                        assert len(store.attempts(owner, workflow.id)) == 1
                        release.set()
                        until(lambda identity=model_call.id: Resources(store).get(owner, identity).status == 'completed', timeout=10)
                        until(lambda identity=attempt.id: store._attempt(identity).stop_confirmed, timeout=10)
                        call = Resources(store).get(owner, model_call.id)
                        assert call.late and call.usage.total_tokens == 11
                        terminal, task = store._attempt(attempt.id), store.task(owner, target.id)
                        assert terminal.state == ('interrupted' if args.operation == 'pause' else 'cancelled')
                        assert task.state == ('retry_wait' if args.operation == 'pause' else 'cancelled')
                        assert task.current_attempt_id is None and (task.paused or args.operation == 'cancel')
                        ledger = store.budget(owner, workflow.id)
                        assert ledger.spent == before_budget.spent and ledger.spent.model_calls == 1
                        assert ledger.reserved.model_calls == ledger.reserved.tool_calls == 0 and not ledger.uncertain
                        assert not Tools(store).calls(owner, workflow.id) and len(server.requests) == before_calls
                        assert len(store.attempts(owner, workflow.id)) == 1
                        assert store.workflow(owner, workflow.id).deadline_at == before_deadline
                        original = source.with_name('displaced-source') if args.root_loss else source
                        assert (original / 'src/parser.py').read_text() == 'value = 0\n'
                        assert (original / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
                        if args.root_loss:
                            assert (source / 'replacement.txt').read_text() == 'Replacement must stay untouched'
                        report['settled_model'] = {'call': call.model_dump(mode='json'),
                            'attempt': terminal.model_dump(mode='json'), 'budget': ledger.model_dump(mode='json'),
                            'no_tool_effect': True, 'owner_bytes_preserved': True, 'exact_replay_without_commands': True}
                        response = {'confirmed': True, 'state': task.state, 'revision': task.revision,
                            'root_task_id': workflow.root_task_id}
                    else:
                        raise AssertionError('Unexpected test-only phase: ' + phase)
                    send(parent, response, max_frame=8192)
                assert process.wait(timeout=10) == 0, (root / 'browser.log').read_text()
            if pipeline and args.stage == 'model':
                assert len(Resources(store).calls(owner, workflow.id)) == len(store.attempts(owner, workflow.id)) == 1
                assert not Tools(store).calls(owner, workflow.id) and len(server.requests) == before_calls
                assert store.budget(owner, workflow.id) == ledger
            if not pipeline:
                db = Store(root / 'state.sqlite')
                try:
                    for table in ('attempts', 'model_calls', 'tool_calls', 'workers'):
                        assert db.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
                    report['no_execution'] = True
                finally:
                    db.close()
        report['passed'] = True
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report['seconds'] = time.monotonic() - started
        assert inventory() == inputs, 'Measured implementation changed'
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'root': str(root), **report}, indent=2), flush=True)


if __name__ == '__main__':
    main()
