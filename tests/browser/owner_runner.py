"""Native browser fixture for the live owner commands, with isolated state."""

import argparse
import hashlib
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tests'))
from conftest import model_server

sys.path.insert(0, str(REPO / 'tests/unit/orchestration'))
from test_application_coordinator import native_app
from test_owner_recipe_profiles import (
    live_parent,
    managed_pipeline,
    parent_ready,
    pinned_parent,
    pipeline_ready,
    wait_handles,
)

import flycode
from flycode.orchestration.attach import AttachmentServer, CoordinatorClient
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.wire import receive, send


def inventory():
    return {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ('src', 'tests') for path in (REPO / name).rglob('*')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def counts(store):
    return {name: store.db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
        for name in ('attempts', 'model_calls', 'tool_calls', 'commands', 'worker_bindings', 'pipelines')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=('preview', 'commands', 'lifecycle', 'recovery'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--helper-first', action='store_true')
    parser.add_argument('--gate-completion', action='store_true')
    args = parser.parse_args()
    if (args.helper_first or args.gate_completion) and args.case != 'lifecycle':
        parser.error('Dependency and completion gates require lifecycle')
    root = args.output.resolve()
    assert not root.is_relative_to(REPO), 'Keep mutable evidence outside measured source'
    root.mkdir(parents=True, exist_ok=False)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    assert node, 'An installed Node runtime is required'
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert Path(flycode.__file__).is_relative_to(expected), flycode.__file__
    inputs = inventory()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    report = {'root': str(root), 'case': args.case, 'helper_first': args.helper_first,
        'gate_completion': args.gate_completion,
        'preparation_succeeded': False, 'passed': False}
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            patch.setenv('FLYCODE_STATE_DATABASE', str(root / 'state.sqlite'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(
                root, server, patch, SimpleNamespace(param=None)))
            prepared = parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server)
            p = stack.enter_context(contextmanager(live_parent.__wrapped__)(
                prepared, managed, patch, SimpleNamespace(param=None)))
            p = pinned_parent.__wrapped__(p)
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            store = p['store']
            if args.case == 'lifecycle':
                prepare_addition(p, helper_first=args.helper_first)
            if args.gate_completion:
                from owner_race import instrument_app, instrument_application

                gate = root / 'completion-gate'
                gate.mkdir()
                instrument_app(patch, gate)
                instrument_application(patch, gate, p['source'], store)
                p['completion_gate'] = gate
            stack.enter_context(AttachmentServer(store.host))
            _, client = stack.enter_context(native_app(p['source'], root / 'history'))
            assert CoordinatorClient(store.path).status().session_id == store.host.session.id
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0 and before['model_calls'] == 1
            report.update(preparation_succeeded=True, before=before, database=str(store.path))
            environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_OWNER_TASK': p['parent'].id, 'FLYCODE_SCREENSHOTS': str(root / 'screenshots'),
                'FLYCODE_OWNER_CASE': args.case, 'FLYCODE_HELPER_FIRST': '1' if args.helper_first else '0',
                'FLYCODE_COMPLETION_GATE': '1' if args.gate_completion else '0'}
            with (root / 'browser.log').open('w') as output:
                if args.case == 'lifecycle':
                    parent, child = socket.socketpair()
                    stack.callback(parent.close)
                    stack.callback(child.close)
                    environment['FLYCODE_PHASE_FD'] = str(child.fileno())
                    with subprocess.Popen([node, str(REPO / 'tests/browser/owner-controls.cjs')],
                            env=environment, pass_fds=(child.fileno(),), stdout=output, stderr=subprocess.STDOUT) as process:
                        child.close()
                        deadline = time.monotonic() + 150
                        try:
                            while process.poll() is None:
                                assert time.monotonic() < deadline, 'Browser deadline'
                                if not select.select([parent], [], [], .2)[0]:
                                    continue
                                try:
                                    message = receive(parent, max_frame=8192)
                                except EOFError:
                                    break
                                try:
                                    if message['phase'] == 'regressions':
                                        run_browser_regression(node, environment, root, 'integrated', p)
                                        response = {'passed': True}
                                    elif message['phase'] == 'historical-materials':
                                        run_browser_regression(node, environment, root, 'materials', p)
                                        response = {'passed': True}
                                    else:
                                        response = execution_phase(p, server, message['phase'], report)
                                except BaseException as error:
                                    report['measurement_error'] = str(error)
                                    send(parent, {'error': str(error)})
                                    process.wait(timeout=10)
                                    raise
                                send(parent, response)
                            result = SimpleNamespace(returncode=process.wait(timeout=10))
                        finally:
                            if process.poll() is None:
                                process.kill()
                    if result.returncode == 0:
                        run_browser_regression(node, environment, root, 'stale', p)
                else:
                    result = subprocess.run([node, str(REPO / 'tests/browser/owner-controls.cjs')],
                        env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=120, check=False)
            after = counts(store)
            report.update(exit_code=result.returncode, after=after)
            if args.case == 'preview':
                assert after == before, 'Owner preview changed canonical state'
            elif result.returncode == 0 and args.case in {'commands', 'recovery'}:
                assert after['attempts'] == after['tool_calls'] == 0 and after['model_calls'] == 1
                assert after['pipelines'] == int(args.case == 'commands') and after['worker_bindings'] == before['worker_bindings'] + 2
            report['passed'] = result.returncode == 0
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        assert inventory() == inputs, 'Measured implementation changed during browser verification'
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
        if (root / 'browser.log').exists():
            print((root / 'browser.log').read_text(), flush=True)
    return 0 if report['passed'] else 1


def run_browser_regression(node, environment, root, stage, p):
    script = 'task-materials.cjs' if stage == 'materials' else 'task-results.cjs'
    task, other = (p['child'], p['parent']) if stage == 'materials' else (p['parent'], p['child'])
    env = {**environment, 'FLYCODE_RESULT_TASK': task.id, 'FLYCODE_RESULT_STAGE': stage,
        'FLYCODE_OTHER_TASK': other.id, 'FLYCODE_SCREENSHOTS': str(root / ('regression-' + stage))}
    with (root / ('regression-' + stage + '.log')).open('w') as output:
        result = subprocess.run([node, str(REPO / 'tests/browser' / script)], env=env,
            stdout=output, stderr=subprocess.STDOUT, timeout=40, check=False)
    assert result.returncode == 0, (root / ('regression-' + stage + '.log')).read_text()


def prepare_addition(p, *, helper_first=False):
    from flycode.domain.orchestration import TaskCommand
    from flycode.domain.verification_control import VerificationProfileDraft
    from flycode.verification.registry import VerificationRegistry

    (p['source'] / 'src/second.py').write_text('value = 0\n')
    store, owner = p['store'], p['owner']
    integrator = store.task(owner, p['integrator'].id)
    store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='browser-addition-budget', changes={'budget': {'model_calls': 0, 'tool_calls': 35}}))
    contract = p['child'].acceptance.model_copy(update={'id': 'browser-added-child'})
    oracle = p['source'].parent / 'browser-addition-oracle'
    (oracle / 'tests').mkdir(parents=True)
    (oracle / 'tests/test_parser.py').write_text('import runpy\ndef test_parse():\n'
        + ('' if helper_first else '    assert runpy.run_path("src/parser.py")["value"] == 1\n') +
        '    assert runpy.run_path("src/second.py")["value"] == 2\n')
    VerificationRegistry(store).register_profile(owner, p['child'].project_id,
        VerificationProfileDraft(title='Explicit additional branch criteria', acceptance=contract,
            checks=p['profile_pins'][p['child'].id].draft.checks), oracle_root=oracle)


def execution_phase(p, server, phase, report):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.integration import Integration

    store, owner, dispatcher, workflow = p['store'], p['owner'], p['dispatcher'], p['workflow']
    if phase == 'invalidate':
        (p['source'] / 'dirty.bin').write_bytes(b'Owner changed source after verified integration')
        return {'changed': True}
    if phase == 'parent-wait':
        first = dispatcher.tick(p['host'], key='browser-parent-prepare')
        assert first.attempt.task_id == p['parent'].id
        wait_handles(dispatcher)
        assert Pipelines(store, dispatcher.root).advance(p['host'], workflow.id) == ()
        report['checkpoint_budget'] = store.budget(owner, workflow.id).model_dump(mode='json')
        report['deadline'] = store.workflow(owner, workflow.id).deadline_at
        return {'stopped': all(attempt.stop_confirmed for attempt in store.attempts(owner, workflow.id))}
    assert phase == 'complete'
    assert store.budget(owner, workflow.id).model_dump(mode='json') == report['checkpoint_budget']
    original = server.respond
    def respond(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return original(request)
        path, value = ('src/second.py', 2) if 'Set second value to 2 ' in messages else ('src/parser.py', 1)
        assert path == 'src/second.py' or 'Repair the parser under the exact owner-approved criteria.' in messages
        return server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': path, 'old_text': 'value = 0', 'new_text': f'value = {value}'}}]}))
    server.respond = respond
    for number in range(25):
        dispatcher.tick(p['host'], key=f'browser-delivery-{number}')
        wait_handles(dispatcher)
        assert store.workflow(owner, workflow.id).deadline_at == report['deadline']
        if store.workflow(owner, workflow.id).state == 'succeeded':
            break
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    parent = store.task(owner, p['parent'].id)
    assert Integration(store, dispatcher.root).current(owner, parent.result_id)
    ledger = store.budget(owner, workflow.id)
    assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain
    assert len(store.attempts(owner, workflow.id)) == 8
    if report['helper_first']:
        child = store.task(owner, p['child'].id)
        helper = next(task for task in store.tasks(owner, workflow.id) if task.title == 'Additional owner branch')
        assert child.dependencies == (helper.id,) and helper.dependencies == ()
        order = [attempt.task_id for attempt in store.attempts(owner, workflow.id)]
        assert order.index(helper.id) < order.index(child.id)
        report['dependency_order'] = order
    assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
    assert (p['source'] / 'src/parser.py').read_text() == 'value = 1\n'
    assert (p['source'] / 'src/extra.py').read_text() == 'value = 4\n'
    report['delivery_budget'] = ledger.model_dump(mode='json')
    if 'completion_gate' in p:
        from owner_race import release

        report['gated_interleaving'] = release(p, p['completion_gate'])
    return {'current': True}


if __name__ == '__main__':
    raise SystemExit(main())
