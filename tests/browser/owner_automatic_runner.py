"""Browser replan after a real automatic, non-retrying independent rejection."""

import argparse
import json
import os
import select
import shutil
import socket
import subprocess
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from owner_runner import (
    counts,
    inventory,
    managed_pipeline,
    model_server,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)
from test_application_coordinator import native_app, owned_service_control, until

import flycode
from flycode.domain.orchestration import BudgetAmount, TaskCommand
from flycode.domain.results import ReviewRecord
from flycode.orchestration.integration import Integration
from flycode.orchestration.wire import receive, send

REPO = Path(__file__).resolve().parents[2]


def configure(p, server):
    store, owner = p['store'], p['owner']
    for name, budget in [('child', BudgetAmount(model_calls=3, tool_calls=2)),
            ('child_review', BudgetAmount(tool_calls=8)), ('integrator', BudgetAmount(tool_calls=35))]:
        task = store.task(owner, p[name].id)
        p[name] = store.command(owner, task.id, TaskCommand(operation='revise',
            expected_revision=task.revision, idempotency_key='automatic-budget-' + name,
            changes={'budget': budget.model_dump()}))
    # Upstream budget revisions invalidate the dependent draft. Reopen through
    # the real owner command before binding/approval, never by changing SQL state.
    task = store.task(owner, p['integrator'].id)
    assert task.state == 'blocked' and task.blocked_reason == 'completion_invalidated'
    p['integrator'] = store.command(owner, task.id, TaskCommand(operation='reopen',
        expected_revision=task.revision, idempotency_key='automatic-reopen-integrator'))
    assert all(task.state == 'planned' for task in store.tasks(owner, p['workflow'].id))
    original, bad_calls = server.respond, []
    def respond(request):
        messages = str(request['messages'])
        if p['parent'].objective in messages:
            return original(request)
        if 'Repair the parser under the exact owner-approved criteria.' in messages:
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = 0', 'new_text': 'value = 1'}}
        else:
            assert p['child'].objective in messages
            bad_calls.append(request)
            assert len(bad_calls) <= 2
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = 0', 'new_text': 'value = -1'}} if len(bad_calls) == 1 else {'tool': 'stop', 'arguments': {}}
        return server.completion(json.dumps({'candidates': [candidate]}))
    server.respond = respond


def observe(p, phase, report):
    store, owner, workflow = p['store'], p['owner'], p['workflow']
    if phase == 'rejected':
        until(lambda: store.task(owner, p['parent'].id).blocked_reason == 'child_review_failed', timeout=65)
        recipe = json.loads(store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (workflow.id,)).fetchone()['data'])
        assert recipe['retry_policies'] == {}
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == 3 and all(attempt.stop_confirmed for attempt in attempts)
        assert store.workflow(owner, workflow.id).state == 'running'
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 3 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'
        rejected = [ReviewRecord.model_validate_json(row['data']) for row in store.db.execute('SELECT data FROM reviews')]
        assert len(rejected) == 1 and rejected[0].status == 'changes_requested'
        report['checkpoint'] = {'budget': ledger.model_dump(mode='json'),
            'deadline': store.workflow(owner, workflow.id).deadline_at,
            'attempts': [attempt.model_dump(mode='json') for attempt in attempts],
            'review': rejected[0].model_dump(mode='json')}
        return {'stopped': True, 'reason': 'child_review_failed'}
    if phase == 'complete':
        until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=90)
        parent = store.task(owner, p['parent'].id)
        assert Integration(store, p['dispatcher'].root).current(owner, parent.result_id)
        attempts = store.attempts(owner, workflow.id)
        authors = [attempt for attempt in attempts if attempt.task_id == p['child'].id]
        assert len(attempts) == 8 and len(authors) == 2
        assert authors[1].fence > authors[0].fence and authors[1].semantic_revision > authors[0].semantic_revision
        assert all(attempt.stop_confirmed for attempt in attempts)
        assert store.workflow(owner, workflow.id).deadline_at == report['checkpoint']['deadline']
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 5 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        old = report['checkpoint']['review']
        assert json.loads(store.db.execute('SELECT data FROM reviews WHERE id=?', (old['id'],)).fetchone()['data']) == old
        assert (p['dispatcher'].root / 'artifacts' / old['artifact_id'] / 'manifest.json').is_file()
        assert (p['dispatcher'].root / 'verification' / old['verdict']['evidence_id'] / 'manifest.json').is_file()
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 1\n'
        assert (p['source'] / 'src/extra.py').read_text() == 'value = 4\n'
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 1\n'
        assert (p['source'] / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        report['delivery'] = {'budget': ledger.model_dump(mode='json'),
            'attempts': [attempt.model_dump(mode='json') for attempt in attempts],
            'events': [event.model_dump(mode='json') for event in store.events(owner, workflow.id)]}
        return {'current': True}
    assert phase == 'invalidate'
    (p['source'] / 'dirty.bin').write_bytes(b'Owner changed source after verified integration')
    return {'changed': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    root.mkdir(parents=True, exist_ok=False)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    assert node
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert Path(flycode.__file__).is_relative_to(expected), flycode.__file__
    inputs, started = inventory(), time.monotonic()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    report = {'passed': False, 'preparation_succeeded': False, 'root': str(root)}
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(
                root, server, patch, SimpleNamespace(param=None)))
            p = pinned_parent.__wrapped__(parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server))
            configure(p, server)
            store = p['store']
            patch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            control, inherited = stack.enter_context(owned_service_control())
            app, client = stack.enter_context(native_app(p['source'], root / 'history', control=inherited))
            inherited.close()
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0 and before['model_calls'] == 1
            report.update(preparation_succeeded=True, before=before, database=str(store.path))
            parent, child = socket.socketpair()
            stack.callback(parent.close)
            stack.callback(child.close)
            environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_OWNER_TASK': p['parent'].id, 'FLYCODE_CHILD_TASK': p['child'].id,
                'FLYCODE_SCREENSHOTS': str(root / 'screenshots'), 'FLYCODE_PHASE_FD': str(child.fileno())}
            with (root / 'browser.log').open('w') as output, subprocess.Popen([node, str(REPO / 'tests/browser/owner-automatic.cjs')], env=environment,
                    pass_fds=(child.fileno(),), stdout=output, stderr=subprocess.STDOUT) as process:
                child.close()
                deadline = time.monotonic() + 190
                try:
                    while process.poll() is None:
                        assert time.monotonic() < deadline, 'Automatic browser deadline'
                        if not select.select([parent], [], [], .2)[0]:
                            continue
                        try:
                            message = receive(parent, max_frame=8192)
                        except EOFError:
                            break
                        if message['phase'] == 'attached':
                            identity = receive(control)
                            assert identity['pid'] == identity['sid'] and identity['pid'] != app.pid
                            report['daemon'] = identity
                            result = {'attached': True}
                        else:
                            result = observe(p, message['phase'], report)
                        send(parent, result)
                    report['exit_code'] = process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        process.kill()
            report['after'] = counts(store)
            report['passed'] = report['exit_code'] == 0
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        assert inventory() == inputs, 'Measured implementation changed'
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key not in {'checkpoint', 'delivery'}}, indent=2), flush=True)
        if (root / 'browser.log').exists():
            print((root / 'browser.log').read_text(), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
