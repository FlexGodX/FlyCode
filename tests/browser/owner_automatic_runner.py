"""Browser recovery from independent rejections, with bounded retry and restart."""

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
    prepare_addition,
)
from test_application_coordinator import APP, native_app, owned_service_control, until

import flycode
from flycode.domain.orchestration import BudgetAmount, TaskCommand
from flycode.domain.results import ReviewRecord
from flycode.orchestration.attach import CoordinatorClient
from flycode.orchestration.host import HostSession
from flycode.orchestration.integration import Integration
from flycode.orchestration.wire import receive, send

REPO = Path(__file__).resolve().parents[2]


def configure(p, server, *, combined=False):
    store, owner = p['store'], p['owner']
    for name, budget in [('child', BudgetAmount(model_calls=3, tool_calls=3 if combined else 2)),
            ('child_review', BudgetAmount(tool_calls=12 if combined else 8)), ('integrator', BudgetAmount(tool_calls=35))]:
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
        elif combined and 'Set second value to 2 before the verified parser repair.' in messages:
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/second.py',
                'old_text': 'value = 0', 'new_text': 'value = 2'}}
        else:
            assert p['child'].objective in messages
            bad_calls.append(request)
            assert len(bad_calls) <= 2
            if combined:
                if len(bad_calls) == 2:
                    assert 'changes_requested' in messages and 'assert -1 == 1' in messages
                candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                    'old_text': 'value = 0' if len(bad_calls) == 1 else 'value = -1',
                    'new_text': 'value = -1' if len(bad_calls) == 1 else 'value = -2'}}
            else:
                candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                    'old_text': 'value = 0', 'new_text': 'value = -1'}} if len(bad_calls) == 1 else {'tool': 'stop', 'arguments': {}}
        return server.completion(json.dumps({'candidates': [candidate]}))
    server.respond = respond


def observe(p, phase, report):
    store, owner, workflow = p['store'], p['owner'], p['workflow']
    combined = report.get('combined', False)
    if phase == 'rejected':
        until(lambda: store.task(owner, p['child'].id).blocked_reason == 'retry_exhausted' if combined else
            store.task(owner, p['parent'].id).blocked_reason == 'child_review_failed', timeout=65)
        until(lambda: store.task(owner, p['parent'].id).blocked_reason in {'child_failed', 'child_review_failed'}, timeout=10)
        recipe = json.loads(store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (workflow.id,)).fetchone()['data'])
        assert bool(recipe['retry_policies']) == combined
        attempts = store.attempts(owner, workflow.id)
        assert len(attempts) == (5 if combined else 3) and all(attempt.stop_confirmed for attempt in attempts)
        assert store.workflow(owner, workflow.id).state == 'running'
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 3 and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'
        assert (p['source'] / 'src/note.py').read_text() == 'phase = 0\n'
        rejected = [ReviewRecord.model_validate_json(row['data']) for row in store.db.execute('SELECT data FROM reviews')]
        assert len(rejected) == (2 if combined else 1) and all(review.status == 'changes_requested' for review in rejected)
        report['checkpoint'] = {'budget': ledger.model_dump(mode='json'),
            'deadline': store.workflow(owner, workflow.id).deadline_at,
            'attempts': [attempt.model_dump(mode='json') for attempt in attempts],
            'review': rejected[0].model_dump(mode='json'),
            'reviews': [review.model_dump(mode='json') for review in rejected]}
        if combined:
            assert store.task(owner, p['child'].id).state == 'failed'
            assert [attempt.state for attempt in attempts if attempt.task_id == p['child'].id] == ['interrupted', 'failed']
        return {'stopped': True, 'reason': 'child_review_failed'}
    if phase == 'mask-failure':
        rows = store.db.execute("SELECT sequence,data FROM events WHERE workflow_id=? AND json_extract(data,'$.kind')='pipeline_retry_blocked'",
            (workflow.id,)).fetchall()
        assert combined and len(rows) == 1
        p['saved_failure_event'] = tuple(rows[0])
        (Path(report['root']) / 'masked-failure-original.json').write_text(json.dumps(
            {'sequence': rows[0]['sequence'], 'raw': rows[0]['data']}, indent=2) + '\n')
        p['masked_attempts'] = store.attempts(owner, workflow.id)
        p['masked_budget'] = store.budget(owner, workflow.id)
        modified = json.loads(rows[0]['data'])
        modified['data']['review_id'] = 'not-the-retained-review'
        store.db.execute('UPDATE events SET data=? WHERE sequence=?', (json.dumps(modified), rows[0]['sequence']))
        return {'masked': True}
    if phase == 'restore-failure':
        sequence, raw = p['saved_failure_event']
        assert store.attempts(owner, workflow.id) == p['masked_attempts']
        assert store.budget(owner, workflow.id) == p['masked_budget']
        store.db.execute('UPDATE events SET data=? WHERE sequence=?', (raw, sequence))
        report['negative_audit'] = {'rejected_without_attempt_or_spend': True, 'original_event_restored': True}
        return {'restored': True}
    if phase == 'complete':
        if report.get('receipt_contention'):
            until(lambda: store.workflow(owner, workflow.id).state == 'succeeded' or any(
                ReviewRecord.model_validate_json(row['data']).status == 'inconclusive'
                for row in store.db.execute('SELECT data FROM reviews')), timeout=90)
            gate = json.loads((Path(report['root']) / 'custody-gate.json').read_text())
            report['custody_gate'] = gate
            assert gate['blocked_reads'] > 0 and gate['guardian_exit'] == 0
            if report['receipt_contention'] == 'held':
                assert gate['last_blocked_at'] - gate['first_blocked_at'] >= 5
                assert not gate.get('released') and 'validated' not in gate
                manifest = json.loads((Path(gate['ref']['directory']).parent / 'manifest.json').read_text())
                invocation = next(item for item in manifest['invocations'] if item.get('process') == gate['ref'])
                assert invocation['state'] == 'interrupted' and invocation['error_class'] == 'cleanup'
                assert not invocation['complete'] and not invocation['stop_confirmed'] and invocation['exit_code'] is None
                assert any('BlockingIOError' in reason for reason in invocation['reasons'])
                ledger = store.budget(owner, workflow.id)
                assert ledger.uncertain and ledger.reserved.tool_calls > 0
                assert ledger.spent.model_calls >= report['checkpoint']['budget']['spent']['model_calls']
                report['held_refusal'] = {'invocation': invocation, 'budget': ledger.model_dump(mode='json'),
                    'still_locked_at_refusal': True}
            assert store.workflow(owner, workflow.id).state == 'succeeded', 'Receipt contention left verification inconclusive'
        until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=180 if combined else 90)
        parent = store.task(owner, p['parent'].id)
        assert Integration(store, p['dispatcher'].root).current(owner, parent.result_id)
        attempts = store.attempts(owner, workflow.id)
        authors = [attempt for attempt in attempts if attempt.task_id == p['child'].id]
        assert len(attempts) == (12 if combined else 8) and len(authors) == (3 if combined else 2)
        assert authors[-1].fence > authors[-2].fence and authors[-1].semantic_revision > authors[-2].semantic_revision
        if combined:
            assert [attempt.state for attempt in authors] == ['interrupted', 'failed', 'succeeded']
            helper = next(task for task in store.tasks(owner, workflow.id) if task.title == 'Additional owner branch')
            assert store.task(owner, p['child'].id).dependencies == (helper.id,)
            order = [attempt.task_id for attempt in attempts]
            assert order.index(helper.id) < max(n for n, identity in enumerate(order) if identity == p['child'].id)
            assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
        assert all(attempt.stop_confirmed for attempt in attempts)
        assert store.workflow(owner, workflow.id).deadline_at == report['checkpoint']['deadline']
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == (6 if combined else 5) and ledger.reserved == BudgetAmount() and not ledger.uncertain
        assert ledger.spent.tool_calls == (55 if combined else 39)
        for old in report['checkpoint']['reviews']:
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
    parser.add_argument('--combined', action='store_true')
    parser.add_argument('--receipt-contention', choices=('transient', 'held'))
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
    report = {'passed': False, 'preparation_succeeded': False, 'root': str(root), 'combined': args.combined,
        'receipt_contention': args.receipt_contention}
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            if args.receipt_contention:
                target = "runpy.run_module('flycode.orchestration.coordinator', run_name='__main__')"
                assert APP.count(target) == 1
                injected = (f'sys.path.insert(0, {str(REPO / "tests/browser")!r})\n'
                    'from owner_custody import instrument\n'
                    f'instrument({str(root / "custody-gate.json")!r}, {args.receipt_contention!r})\n' + target)
                patch.setitem(native_app.__wrapped__.__globals__, 'APP', APP.replace(target, injected))
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(
                root, server, patch, SimpleNamespace(param=None)))
            p = pinned_parent.__wrapped__(parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server))
            configure(p, server, combined=args.combined)
            if args.combined:
                prepare_addition(p, helper_first=True)
            store = p['store']
            patch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            service_stack = stack.enter_context(ExitStack())
            app_stack = stack.enter_context(ExitStack())
            control, inherited = service_stack.enter_context(owned_service_control())
            app, client = app_stack.enter_context(native_app(p['source'], root / 'history', control=inherited))
            inherited.close()
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0 and before['model_calls'] == 1
            report.update(preparation_succeeded=True, before=before, database=str(store.path))
            parent, child = socket.socketpair()
            stack.callback(parent.close)
            stack.callback(child.close)
            environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_OWNER_TASK': p['parent'].id, 'FLYCODE_CHILD_TASK': p['child'].id,
                'FLYCODE_SCREENSHOTS': str(root / 'screenshots'), 'FLYCODE_PHASE_FD': str(child.fileno()),
                'FLYCODE_OWNER_COMBINED': '1' if args.combined else '0'}
            with (root / 'browser.log').open('w') as output, subprocess.Popen([node, str(REPO / 'tests/browser/owner-automatic.cjs')], env=environment,
                    pass_fds=(child.fileno(),), stdout=output, stderr=subprocess.STDOUT) as process:
                child.close()
                deadline = time.monotonic() + (300 if args.combined else 190)
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
                        elif message['phase'] == 'restart':
                            assert args.combined
                            previous = CoordinatorClient(store.path).status()
                            attempts, budget = store.attempts(p['owner'], p['workflow'].id), store.budget(p['owner'], p['workflow'].id)
                            app_stack.close()
                            assert CoordinatorClient(store.path).status() == previous, 'Daemon must survive app close'
                            service_stack.close()
                            stopped = HostSession.model_validate_json(store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()['data'])
                            assert stopped.id == previous.session_id and stopped.phase == 'stopped'
                            assert store.attempts(p['owner'], p['workflow'].id) == attempts
                            assert store.budget(p['owner'], p['workflow'].id) == budget
                            report['restart'] = {'previous': previous.model_dump(mode='json'), 'stopped': stopped.model_dump(mode='json')}
                            control, inherited = service_stack.enter_context(owned_service_control())
                            app, client = app_stack.enter_context(native_app(p['source'], root / 'history', control=inherited))
                            inherited.close()
                            result = {'url': str(client.base_url).rstrip('/')}
                        elif message['phase'] == 'reattached':
                            identity = receive(control)
                            assert identity['pid'] == identity['sid'] and identity['pid'] != app.pid
                            current = CoordinatorClient(store.path).status()
                            previous = report['restart']['previous']
                            assert current.registration_id == previous['registration_id'] and current.generation > previous['generation']
                            assert current.session_id != previous['session_id']
                            assert store.attempts(p['owner'], p['workflow'].id) == attempts
                            assert store.budget(p['owner'], p['workflow'].id) == budget
                            assert store.workflow(p['owner'], p['workflow'].id).deadline_at == report['checkpoint']['deadline']
                            report['restart']['current'] = current.model_dump(mode='json')
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
