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
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests'))

from conftest import model_server

sys.path.insert(0, str(REPO / 'tests/unit/orchestration'))

from test_application_coordinator import (
    managed_pipeline,
    native_app,
    owned_service_control,
    until,
)

from flycode.orchestration.host import Host
from flycode.orchestration.store import Store
from flycode.orchestration.tools import Tools
from flycode.orchestration.wire import receive, send
from flycode.verification.custody import stopped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('attach', 'lost-stop', 'runtime'), required=True)
    parser.add_argument('--stage', choices=('slow-review', 'slow-integration'), default='slow-review')
    parser.add_argument('--operation', choices=('pause', 'cancel'), default='pause')
    parser.add_argument('--root-loss', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.output or Path(tempfile.mkdtemp(prefix='flycode-coordinator-browser-'))
    root.mkdir(parents=True, exist_ok=True)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    assert node, 'Set FLYCODE_NODE to an installed Node executable'
    report = {'case': args.case, 'stage': args.stage, 'operation': args.operation,
        'root_loss': args.root_loss, 'passed': False, 'phases': []}
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
            parent, child = socket.socketpair()
            stack.callback(parent.close)
            stack.callback(child.close)
            parent.settimeout(10)
            environment = {**os.environ, 'FLYCODE_UI_URL': str(first_http.base_url).rstrip('/'),
                'FLYCODE_SECOND_UI_URL': str(second_http.base_url).rstrip('/'),
                'FLYCODE_SCREENSHOTS': str(root / 'screenshots'), 'FLYCODE_BROWSER_CASE': args.case,
                'FLYCODE_PHASE_FD': str(child.fileno()), 'FLYCODE_STOP_OPERATION': args.operation,
                'FLYCODE_ROOT_LOSS': '1' if args.root_loss else '0'}
            if pipeline:
                target = tasks[2] if args.stage == 'slow-review' else tasks[3]
                environment.update(FLYCODE_TASK_ID=target.id, FLYCODE_TASK_TITLE=target.title)
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
                    else:
                        raise AssertionError('Unexpected test-only phase: ' + phase)
                    send(parent, response, max_frame=8192)
                assert process.wait(timeout=10) == 0, (root / 'browser.log').read_text()
            if not pipeline:
                db = Store(root / 'state.sqlite')
                try:
                    for table in ('attempts', 'model_calls', 'tool_calls', 'workers'):
                        assert db.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
                    report['no_execution'] = True
                finally:
                    db.close()
            report['passed'] = True
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'root': str(root), **report}, indent=2), flush=True)


if __name__ == '__main__':
    main()
