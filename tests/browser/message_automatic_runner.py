"""Detached coordinator, owner form, real sibling exchange and fresh verification."""

import argparse
import hashlib
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
from message_scenario import configure
from owner_runner import (
    REPO,
    counts,
    inventory,
    managed_pipeline,
    model_server,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)
from test_application_coordinator import APP, native_app, owned_service_control, until

import flycode
from flycode.domain.results import ReviewRecord
from flycode.orchestration.attach import CoordinatorClient
from flycode.orchestration.integration import Integration
from flycode.orchestration.messages import Messages
from flycode.orchestration.wire import receive, send


def source_bytes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}


def traced_app(root):
    log = root / 'http-requests.jsonl'
    wrapper = f"""
class Traced:
    def __init__(self, app):
        self.app, self.sequence = app, 0
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        self.sequence += 1
        sequence, started = self.sequence, time.monotonic()
        def record(phase):
            with open({str(log)!r}, 'a') as output:
                output.write(json.dumps(dict(sequence=sequence, phase=phase, path=scope['path'],
                    elapsed=time.monotonic()-started))+'\\n')
        record('start')
        try:
            return await self.app(scope, receive, send)
        finally:
            record('end')
"""
    anchor = 'sock = socket.socket()'
    factory = 'create_app(Path(sys.argv[1]), Path(sys.argv[2]))'
    assert APP.count(anchor) == APP.count(factory) == 1
    return APP.replace(anchor, wrapper + '\n' + anchor).replace(factory, 'Traced(' + factory + ')')


def http_idle(root):
    active = set()
    for line in (root / 'http-requests.jsonl').read_text().splitlines():
        value = json.loads(line)
        if value['phase'] == 'start':
            active.add(value['sequence'])
        else:
            active.discard(value['sequence'])
    return not active


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    root.mkdir(parents=True, exist_ok=False)
    node = shutil.which(os.environ.get('FLYCODE_NODE', 'node'))
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert node and Path(flycode.__file__).is_relative_to(expected)
    inputs, started = inventory(), time.monotonic()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    report = {'passed': False, 'prepared': False, 'root': str(root)}
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            patch.setitem(native_app.__wrapped__.__globals__, 'APP', traced_app(root))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(root, server, patch, SimpleNamespace(param=None)))
            p = parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server)
            scenario = configure(p, server, root)
            p = pinned_parent.__wrapped__(p)
            store, owner, workflow = p['store'], p['owner'], p['workflow']
            before_source = source_bytes(p['source'])
            assert len(store.tasks(owner, workflow.id)) == 7
            assert store.db.execute('SELECT count(*) FROM instances').fetchone()[0] == 4
            patch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            control, inherited = stack.enter_context(owned_service_control())
            app, client = stack.enter_context(native_app(p['source'], root / 'history', control=inherited))
            inherited.close()
            gates = scenario['gates']

            def release_all():
                for name in ('a1', 'a2', 'boot', 'b'):
                    (gates / (name + '-release')).touch()

            stack.callback(release_all)
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0 and before['model_calls'] == 1
            report.update(prepared=True, before=before, database=str(store.path))
            parent, child = socket.socketpair()
            stack.callback(parent.close)
            stack.callback(child.close)
            env = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
                'FLYCODE_OWNER_TASK': p['parent'].id, 'FLYCODE_CHILD_TASK': p['child'].id,
                'FLYCODE_SECOND_TASK': p['second'].id, 'FLYCODE_SCREENSHOTS': str(root / 'screenshots'),
                'FLYCODE_PHASE_FD': str(child.fileno())}
            with (root / 'browser.log').open('w') as output, subprocess.Popen(
                    [node, str(REPO / 'tests/browser/message-automatic.cjs')], env=env,
                    pass_fds=(child.fileno(),), stdout=output, stderr=subprocess.STDOUT) as process:
                child.close()
                deadline = time.monotonic() + 200
                try:
                    while process.poll() is None:
                        assert time.monotonic() < deadline, 'Automatic message browser deadline'
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
                            report['host'] = CoordinatorClient(store.path).status().model_dump(mode='json')
                            result = {'attached': True}
                        elif message['phase'] == 'disconnected':
                            until(lambda: http_idle(root), timeout=15)
                            result = {'idle': True}
                        elif message['phase'] == 'workers-waiting':
                            until(lambda: (gates / 'a1-started').exists() and (gates / 'boot-started').exists(), timeout=30)
                            attempts = store.attempts(owner, workflow.id)
                            active = [attempt for attempt in attempts if attempt.state == 'running']
                            assert {attempt.task_id for attempt in active} == {p['child'].id, p['second'].id}
                            report['deadline'] = store.workflow(owner, workflow.id).deadline_at
                            assert report['deadline'] is not None
                            report['peers'] = [attempt.model_dump(mode='json') for attempt in active]
                            result = {'waiting': True}
                        elif message['phase'] == 'exchange':
                            assert store.db.execute('SELECT count(*) FROM message_route_grants').fetchone()[0] == 1
                            (gates / 'boot-release').touch()
                            until(lambda: (gates / 'b-started').exists(), timeout=20)
                            questions = [item for item in Messages(store).journal(owner, workflow.id) if item.content.kind == 'question']
                            assert len(questions) == 1
                            (gates / 'a1-release').touch()
                            until(lambda: (gates / 'a2-started').exists(), timeout=20)
                            answers = [item for item in Messages(store).journal(owner, workflow.id) if item.content.kind == 'answer']
                            assert len(answers) == 1 and answers[0].causation_id == questions[0].id
                            (gates / 'b-release').touch()
                            until(lambda: (gates / 'answer-consumed').exists(), timeout=20)
                            assert (gates / 'answer-consumed').read_text() == answers[0].id
                            def accepted_answer(identity=answers[0].id):
                                row = store.db.execute('SELECT data FROM message_inbox WHERE message_id=?', (identity,)).fetchone()
                                return row is not None and json.loads(row['data'])['outcome'] == 'received'
                            until(accepted_answer, timeout=10)
                            (gates / 'a2-release').touch()
                            until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=100)
                            current = store.task(owner, p['parent'].id)
                            assert Integration(store, p['dispatcher'].root).current(owner, current.result_id)
                            reviews = [ReviewRecord.model_validate_json(row['data']) for row in store.db.execute('SELECT data FROM reviews')]
                            assert len(reviews) == 3 and all(review.status == 'approved' for review in reviews)
                            assert len({review.reviewer_attempt_id for review in reviews}) == 3
                            attempts = store.attempts(owner, workflow.id)
                            assert len(attempts) == 8 and all(attempt.stop_confirmed for attempt in attempts)
                            ledger = store.budget(owner, workflow.id)
                            assert ledger.spent.model_calls == 10 and ledger.spent.tool_calls <= 120
                            assert ledger.reserved.model_calls == ledger.reserved.tool_calls == 0 and not ledger.uncertain
                            assert store.workflow(owner, workflow.id).deadline_at == report['deadline']
                            after_source = source_bytes(p['source'])
                            assert set(after_source) == set(before_source)
                            changed = sorted(name for name in before_source if before_source[name] != after_source[name])
                            assert changed == ['src/extra.py', 'src/note.py', 'src/parser.py', 'src/second.py']
                            assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
                            report.update(budget=ledger.model_dump(mode='json'), changed_paths=changed,
                                reviews=[review.model_dump(mode='json') for review in reviews],
                                attempts=[attempt.model_dump(mode='json') for attempt in attempts],
                                messages=[item.model_dump(mode='json') for item in Messages(store).journal(owner, workflow.id)],
                                model_actions=scenario['observed'], model_counts=scenario['calls'])
                            result = {'current': True, 'question_id': questions[0].id, 'answer_id': answers[0].id}
                        else:
                            raise AssertionError(message)
                        send(parent, result)
                    report['exit_code'] = process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
            assert report['exit_code'] == 0, (root / 'browser.log').read_text()
            report.update(after=counts(store))
            (root / 'model-requests.json').write_text(json.dumps(server.requests, indent=2) + '\n')
        report['passed'] = True
    except BaseException as error:
        report['passed'] = False
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        report['inputs_unchanged'] = inventory() == inputs
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        assert report['inputs_unchanged'], 'Source changed during automatic verification'


if __name__ == '__main__':
    main()
