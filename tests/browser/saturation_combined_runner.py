"""Native message/retry/replan pressure at the developer and tool boundaries."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from owner_runner import REPO, counts, inventory, managed_pipeline, model_server
from saturation_scenario import NAMES, prepare
from test_managed_coordinator import until as wait_until

import flycode
from flycode.domain.agent_requests import AgentTaskRequest
from flycode.domain.message_routes import MessageRouteEdge
from flycode.domain.orchestration import BudgetAmount
from flycode.domain.results import ReviewRecord
from flycode.domain.verification import EvidenceManifest
from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.agent_requests import AgentRequests
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.coordinator import Coordinator
from flycode.orchestration.host import Host
from flycode.orchestration.message_routes import MessageRoutes
from flycode.orchestration.messages import Messages
from flycode.orchestration.owner_requests import OwnerMessageRoutes
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.projects import Projects
from flycode.orchestration.runtime import Runtime
from flycode.orchestration.store import StoreError
from flycode.orchestration.tools import Tools
from flycode.verification.custody import stopped
from flycode.verification.evidence import read_regular

REPAIR = 'Owner graph revision repair: set parser value to 1 under the unchanged criteria.'
GATES = (*NAMES[:6], 'parser_reply', 'second_answer')


def entries(prompt):
    result = []
    for line in prompt.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and {'message_id', 'sender_id', 'content'} <= set(value):
            result.append(value)
    return result


def routes(p, grant_id, edges, key):
    store, owner, workflow = p['store'], p['owner'], p['workflow']
    project = Projects(store).resolve(p['source'])
    return MessageRoutes(store).approve(owner, project, OwnerMessageRoutes(operation='message_routes',
        project_id=project.id, project_revision=project.revision, pipeline_id=p['recipe'].id,
        expected_revision=store.workflow(owner, workflow.id).revision,
        task_revisions={task.id: task.revision for task in store.tasks(owner, workflow.id)},
        expected_grant_id=grant_id, edges=edges, idempotency_key=key))


def provider(p, server):
    calls, observed, unlocked = {}, [], []
    derived = None

    def gate(name):
        return {'tool': 'run_tests', 'arguments': {'target': f'tests/test_saturation_gate.py::test_{name}', 'timeout': 120}}

    def respond(request):
        nonlocal derived
        # Observe a real independent SQL writer during every paid inference.
        connection = sqlite3.connect(p['store'].path, timeout=2)
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
            unlocked.append(len(unlocked) + 1)
        finally:
            connection.close()
        prompt = request['messages'][-1]['content']
        if p['parent'].objective in prompt:
            name = 'root'
        elif REPAIR in prompt:
            name = 'revised'
        else:
            name, = [name for name, task in p['children'].items() if task.objective in prompt]
        calls[name] = calls.get(name, 0) + 1
        number = calls[name]
        if name == 'root':
            assert number == 1
            action = {'tool': 'edit_file', 'arguments': {'path': 'src/note.py', 'old_text': 'phase = 0', 'new_text': 'phase = 1'}}
        elif name == 'revised':
            assert number <= 2
            action = ({'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = 0', 'new_text': 'value = 1'}} if number == 1 else {'tool': 'stop', 'arguments': {}})
        elif name == 'parser':
            assert number <= 6
            if number == 1:
                action = gate('parser')
            elif number == 2:
                question, = [m for m in entries(prompt) if m['content']['kind'] == 'question']
                action = {'tool': 'send_message', 'arguments': {'recipients': [question['sender_id']],
                    'causation_id': question['message_id'], 'content': {'kind': 'answer', 'answer': 'Use parser result 1.'}}}
            elif number == 3:
                action = gate('parser_reply')
            elif number == 4:
                action = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'}}
            else:
                assert 'changes_requested' in prompt and 'value = -1' in prompt and 'tests/test_parser.py::test_parse' in prompt
                action = ({'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                    'old_text': 'value = -1', 'new_text': 'value = -2'}} if number == 5 else {'tool': 'stop', 'arguments': {}})
        elif name == 'second':
            assert number <= 30
            if number == 1:
                action = gate('second')
            elif number == 2:
                match = re.search(r'Host-approved send_message recipients: (\[[^\n]+\])', prompt)
                assert match
                peers = json.loads(match.group(1))
                assert len(peers) == 1
                action = {'tool': 'send_message', 'arguments': {'recipients': peers,
                    'content': {'kind': 'question', 'question': 'Which parser result should the second value use?'}}}
            elif number == 3:
                action = gate('second_answer')
            elif number == 4:
                answer, = [m for m in entries(prompt) if m['content']['kind'] == 'answer']
                match = re.fullmatch(r'Use parser result ([0-9]+)\.', answer['content']['answer'])
                assert match
                derived = int(match.group(1)) + 1
                (p['gates'] / 'answer-consumed').write_text(answer['message_id'])
                observed.append({'kind': 'answer_consumed', 'message_id': answer['message_id'], 'derived': derived})
                action = {'tool': 'read_file', 'arguments': {'path': 'src/second.py'}}
            elif number < 30:
                action = {'tool': 'read_file', 'arguments': {'path': f'src/{NAMES[number - 3]}.py'}}
            else:
                assert derived == 2 and 'value = 0' in prompt
                # Controlled provider state retains the earlier parsed answer;
                # this schedule does not measure stateless model recall quality.
                action = {'tool': 'edit_file', 'arguments': {'path': 'src/second.py', 'old_text': 'value = 0', 'new_text': f'value = {derived}'}}
        elif name == NAMES[5]:
            assert number <= 3
            action = (gate(name) if number == 1 else {'tool': 'read_file', 'arguments': {'path': f'src/{name}.py'}} if number == 2
                else {'tool': 'edit_file', 'arguments': {'path': f'src/{name}.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}})
        else:
            assert name in NAMES[2:5] and number <= 2
            action = (gate(name) if number == 1 else {'tool': 'edit_file', 'arguments': {
                'path': f'src/{name}.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}})
        observed.append({'kind': 'provider_action', 'name': name, 'number': number, 'action': action, 'at': time.time()})
        return server.completion(json.dumps({'candidates': [action]}))

    server.respond = respond
    return calls, observed, unlocked


def replan_arguments(p, key):
    return {'changes': {p['children']['parser'].id: {'objective': REPAIR}},
        'expected_revision': p['store'].workflow(p['owner'], p['workflow'].id).revision,
        'expected_revisions': {t.id: t.revision for t in p['store'].tasks(p['owner'], p['workflow'].id)},
        'profile_pins': p['profile_pins'], 'key': key}


def run(p, server, report):
    store, owner, runtime, workflow = (p[k] for k in ('store', 'owner', 'runtime', 'workflow'))
    gates, dispatcher, stop = p['gates'], p['dispatcher'], Event()
    source_before = {str(path.relative_to(p['source'])): path.read_bytes() for path in p['source'].rglob('*') if path.is_file()}
    a, b, f = (p['children'][name] for name in ('parser', 'second', NAMES[5]))
    grant = routes(p, None, (MessageRouteEdge(sender_task_id=a.id, recipient_task_id=b.id),
        MessageRouteEdge(sender_task_id=b.id, recipient_task_id=a.id)), 'limits-routes')
    calls, observed, unlocked = provider(p, server)
    original_pipeline = store.db.execute('SELECT data FROM pipelines WHERE id=?', (p['recipe'].id,)).fetchone()[0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(Coordinator(store.host).run, stop.is_set)
        def until(predicate, *, timeout):
            def checked():
                if running.done():
                    running.result()
                    raise AssertionError('Coordinator exited before the scenario finished')
                return predicate()
            return wait_until(checked, timeout=timeout)
        try:
            until(lambda: all((gates / (name + '-started')).exists() for name in NAMES[:4]), timeout=50)
            active = [item for item in store.attempts(owner, workflow.id) if item.state == 'running']
            assert len(active) == 4
            workers = [Runtime(store, dispatcher.root).worker(owner, item.id) for item in active]
            assert all(worker.state == 'running' and not worker.stop_confirmed for worker in workers)
            assert len({worker.pid for worker in workers}) == 4 and all(worker.pid for worker in workers)
            deadline = store.workflow(owner, workflow.id).deadline_at
            before = store.budget(owner, workflow.id)
            arguments = replan_arguments(p, 'limits-live-replan')
            for _ in range(2):
                with pytest.raises(StoreError) as error:
                    Pipelines(store, dispatcher.root).replan(owner, p['recipe'].id, **arguments)
                assert error.value.code == 'stop_unconfirmed'
            assert store.budget(owner, workflow.id) == before
            project = Projects(store).resolve(p['source'])
            instance = next(store.instance(runtime, item.id) for item in p['instances'] if store.instance(runtime, item.id).state == 'idle')
            issued = AgentRequests(store).issue(owner, project, instance_id=instance.id, expected_revision=instance.revision, key='limits-fifth-context')
            echo = p['children']['echo']
            request = AgentTaskRequests(store).submit(instance.id, issued.credential, AgentTaskRequest(
                expected_instance_revision=instance.revision, capabilities=('python',), preferred_task_id=echo.id,
                expected_task_revision=store.task(owner, echo.id).revision, idempotency_key='limits-fifth'))
            refused = dispatcher.select(runtime, key='limits-full-pool', agent_request_id=request.id, finalize_request=False)
            assert refused.attempt is None and 'instance_limit' in refused.reasons
            assert AgentTaskRequests(store).get(instance.id, issued.credential, request.id).state == 'queued'
            charlie = next(item for item in active if item.task_id == p['children']['charlie'].id)
            (gates / 'charlie-release').touch()
            until(lambda: (gates / 'echo-started').exists(), timeout=30)
            assert store._attempt(charlie.id).stop_confirmed
            fifth = next(item for item in store.attempts(owner, workflow.id) if item.task_id == echo.id)
            events = store.events(owner, workflow.id)
            assert next(n for n, event in enumerate(events) if event.kind == 'worker_stopped' and event.attempt_id == charlie.id) < next(
                n for n, event in enumerate(events) if event.kind == 'attempt_assigned' and event.attempt_id == fifth.id)
            (gates / 'second-release').touch()
            until(lambda: (gates / 'second_answer-started').exists(), timeout=20)
            (gates / 'parser-release').touch()
            until(lambda: (gates / 'parser_reply-started').exists(), timeout=20)
            question, = [m for m in Messages(store).journal(owner, workflow.id) if m.content.kind == 'question']
            answer, = [m for m in Messages(store).journal(owner, workflow.id) if m.content.kind == 'answer']
            assert answer.causation_id == question.id
            (gates / 'second_answer-release').touch()
            until(lambda: (gates / 'answer-consumed').exists(), timeout=20)
            def received():
                row = store.db.execute('SELECT data FROM message_inbox WHERE message_id=?', (answer.id,)).fetchone()
                return row is not None and json.loads(row[0])['outcome'] == 'received'
            until(received, timeout=10)
            for name in ('delta', 'echo'):
                (gates / (name + '-release')).touch()
            # Keep the addressed A endpoint stable through B's final response.
            until(lambda: any(item.task_id == b.id and item.stop_confirmed
                for item in store.attempts(owner, workflow.id)), timeout=100)
            (gates / 'parser_reply-release').touch()
            until(lambda: store.task(owner, a.id).blocked_reason == 'retry_exhausted', timeout=240)
            until(lambda: all(store.task(owner, p['children'][name].id).completion_id for name in NAMES[1:5]), timeout=300)
            until(lambda: all(item.stop_confirmed for item in store.attempts(owner, workflow.id)), timeout=60)
            until(lambda: store.task(owner, p['parent'].id).blocked_reason in {'child_failed', 'child_review_failed'}, timeout=20)
            authors = [item for item in store.attempts(owner, workflow.id) if item.task_id == a.id]
            assert len(authors) == 2
            artifacts = [Artifacts(store, dispatcher.root).for_attempt(owner, item.id) for item in authors]
            assert [(Artifacts(store, dispatcher.root).directory / item.id / 'target/src/parser.py').read_text()
                for item in artifacts] == ['value = -1\n', 'value = -2\n']
            assert store.budget(owner, workflow.id).reserved == BudgetAmount()
            routes(p, grant.id, (), 'limits-retire-routes')
            reservations = {row['attempt_id']: tuple(row) for row in store.db.execute('SELECT * FROM reservations')}
            arguments = replan_arguments(p, 'limits-owner-replan')
            recipe = Pipelines(store, dispatcher.root).replan(owner, p['recipe'].id, **arguments)
            assert Pipelines(store, dispatcher.root).replan(owner, p['recipe'].id, **arguments) == recipe and recipe.graph_revision == 1
            assert {row['attempt_id']: tuple(row) for row in store.db.execute('SELECT * FROM reservations')
                if row['attempt_id'] in reservations} == reservations
            assert store.db.execute('SELECT data FROM pipelines WHERE id=?', (p['recipe'].id,)).fetchone()[0] == original_pipeline
            (gates / (NAMES[5] + '-release')).touch()
            def boundary_reached():
                for item in store.attempts(owner, workflow.id):
                    if item.task_id == a.id and item.semantic_revision == store.task(owner, a.id).semantic_revision and item.stop_confirmed:
                        worker = Runtime(store, dispatcher.root).worker(owner, item.id)
                        if worker.result_hash is None:
                            raise AssertionError('Revised worker stopped without a result: ' + str(worker.error))
                return store.budget(owner, workflow.id).spent.tool_calls == 120
            until(boundary_reached, timeout=300)
            until(lambda: all(item.stop_confirmed for item in store.attempts(owner, workflow.id)), timeout=60)
            ledger = store.budget(owner, workflow.id)
            assert ledger.spent == BudgetAmount(model_calls=48, tool_calls=120)
            assert ledger.reserved == BudgetAmount() and not ledger.uncertain and ledger.free == BudgetAmount(model_calls=12)
            assert len(unlocked) == 48
            attempts = store.attempts(owner, workflow.id)
            assert len(attempts) == 17 and len([item for item in attempts if item.task_id == a.id]) == 3
            final = next(item for item in attempts if item.task_id == f.id)
            assert final.initial_budget == BudgetAmount(model_calls=3, tool_calls=3)
            review_attempt = next(item for item in attempts if item.task_id == p['review_by_name'][NAMES[5]].id)
            assert review_attempt.initial_budget == BudgetAmount(tool_calls=2)
            reviews = [ReviewRecord.model_validate_json(row[0]) for row in store.db.execute('SELECT data FROM reviews')]
            assert len(reviews) == 8
            last, = [item for item in reviews if item.reviewer_attempt_id == review_attempt.id]
            assert last.status == 'inconclusive' and last.verdict.status != 'passed'
            final_checks = [item for item in Tools(store).calls(owner, workflow.id) if item.attempt_id == review_attempt.id]
            assert len(final_checks) == 2 and all(item.process is not None for item in final_checks)
            raw = read_regular(dispatcher.root / 'verification' / last.verdict.evidence_id / 'manifest.json')
            assert hashlib.sha256(raw).hexdigest() == last.verdict.evidence_hash
            manifest = EvidenceManifest.model_validate_json(raw)
            denied = [item for item in manifest.invocations if item['reasons'] == ['budget_exhausted']]
            assert len(denied) == 1 and not manifest.complete
            assert denied[0]['state'] == 'cancelled' and denied[0]['error_class'] == 'policy'
            assert not denied[0]['process_started'] and denied[0]['process'] is None and denied[0]['argv'] == []
            assert sum(item.status == 'changes_requested' for item in reviews) == 2
            assert store.task(owner, f.id).completion_id is None
            assert store.workflow(owner, workflow.id).state != 'succeeded'
            assert store.workflow(owner, workflow.id).deadline_at == deadline
            assert len(store.tasks(owner, workflow.id)) == 64 and not store.db.execute('SELECT 1 FROM integrations').fetchone()
            receipts = []
            for item in attempts:
                if item.profile_snapshot.role == 'developer':
                    process = Runtime(store, dispatcher.root).worker(owner, item.id).process
                    with stopped(process) as receipt:
                        assert receipt.stop_confirmed
                        receipts.append({'kind': 'worker', 'id': item.id, 'stop_confirmed': True})
            tools = Tools(store).calls(owner, workflow.id)
            for item in tools:
                if item.process is not None:
                    with stopped(item.process) as receipt:
                        assert receipt.stop_confirmed
                        receipts.append({'kind': 'check', 'id': item.id, 'stop_confirmed': True})
            assert len(receipts) == 83 and len(tools) == 120
            assert all(store.task(owner, p['children'][name].id).state == 'planned'
                and store.task(owner, p['children'][name].id).completion_id is None for name in NAMES[6:])
            assert store.db.execute("SELECT COUNT(*) FROM events WHERE json_extract(data,'$.kind')='pipeline_replanned'").fetchone()[0] == 1
            observed_counts = counts(store)
            stable = time.monotonic() + 3
            while time.monotonic() < stable:
                assert not running.done()
                current = counts(store)
                assert all(current[key] == observed_counts[key] for key in ('attempts', 'model_calls', 'tool_calls', 'worker_bindings', 'pipelines'))
                assert store.budget(owner, workflow.id) == ledger
                time.sleep(.05)
            assert {str(path.relative_to(p['source'])): path.read_bytes() for path in p['source'].rglob('*') if path.is_file()} == source_before
            report.update(passed=True, safe_incomplete=True, budget=ledger.model_dump(mode='json'), attempts=len(attempts),
                provider_counts=calls, observed=observed, unlocked_sql_http=len(unlocked), receipts=receipts,
                current_completion=False, remaining_scientific_gates=True)
        finally:
            for name in GATES:
                (gates / (name + '-release')).touch()
            stop.set()
            running.result(timeout=60)


def trace_wire(root, patch):
    from threading import Lock, current_thread

    from flycode.orchestration import wire

    receive, send, lock = wire.receive, wire.send, Lock()
    def record(kind, channel, value):
        data = {'at': time.time(), 'kind': kind, 'fd': channel.fileno(), 'thread': current_thread().name, **value}
        with lock, (root / 'wire-trace.jsonl').open('a') as stream:
            stream.write(json.dumps(data) + '\n')
    def observed_receive(channel, **kwargs):
        try:
            value = receive(channel, **kwargs)
        except BaseException as error:
            record('receive_error', channel, {'error': type(error).__name__, 'message': str(error)[:1000]})
            raise
        data = {'operation': value.get('operation'), 'sequence': value.get('sequence')}
        if data['operation'] == 'error':
            data['payload'] = value.get('payload')
        elif data['operation'] == 'result':
            result = value.get('payload', {})
            data['result'] = {key: result.get(key) for key in ('success', 'steps', 'tool_calls')}
        record('receive', channel, data)
        return value
    def observed_send(channel, value, **kwargs):
        data = {'sequence': value.get('sequence'), 'attempt_id': value.get('attempt_id'), 'error': value.get('error')}
        record('send_start', channel, data)
        try:
            result = send(channel, value, **kwargs)
        except BaseException as error:
            record('send_error', channel, {**data, 'error': type(error).__name__, 'message': str(error)[:1000]})
            raise
        record('send_end', channel, data)
        return result
    patch.setattr(wire, 'receive', observed_receive)
    patch.setattr(wire, 'send', observed_send)

    import traceback

    from flycode.orchestration.store import Store

    transaction = Store.transaction
    @contextmanager
    def observed_transaction(store):
        started, acquired, error_name = time.monotonic(), None, None
        try:
            with transaction(store):
                acquired = time.monotonic()
                yield
        except BaseException as error:
            error_name = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            ended = time.monotonic()
            waited = (acquired if acquired is not None else ended) - started
            held = None if acquired is None else ended - acquired
            if error_name or waited > .05 or (held is not None and held > .05):
                data = {'at': time.time(), 'thread': current_thread().name,
                    'wait_seconds': waited, 'hold_seconds': held, 'error': error_name,
                    'stack': traceback.format_stack(limit=8)}
                with lock, (root / 'transaction-trace.jsonl').open('a') as stream:
                    stream.write(json.dumps(data) + '\n')
    patch.setattr(Store, 'transaction', observed_transaction)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    assert Path(flycode.__file__).is_relative_to(Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src'))))
    root.mkdir(parents=True, exist_ok=False)
    inputs, started = inventory(), time.monotonic()
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    report = {'passed': False, 'prepared': False, 'prepare_only': args.prepare_only}
    server = None
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            server.contexts = {name: 16384 for name in server.models}
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(root, server, patch, SimpleNamespace(param=None)))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            trace_wire(root, patch)
            leader = stack.enter_context(Host(managed[0].path, managed[6]))
            store = leader.connect(catalogue=managed[0].catalogue)
            stack.callback(store.close)
            p = prepare(managed, store, server, root, resource_pressure=True)
            assert len(store.tasks(p['owner'], p['workflow'].id)) == 64
            assert counts(store)['attempts'] == counts(store)['tool_calls'] == 0 and len(server.requests) == counts(store)['model_calls'] == 1
            assert 'managed-host:' + store.host.session.id == leader._scope(store).actor_id
            assert p['recipe'].reruns == 2 and p['recipe'].retry_policies[p['children']['parser'].id].max_retries == 1
            assert all(pin.draft.reruns == 2 for pin in p['profile_pins'].values())
            report.update(prepared=True, database=str(store.path), tasks=64, before=counts(store), configured_context=16384)
            if not args.prepare_only:
                run(p, server, report)
    except BaseException as error:
        report['passed'] = False
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        report['inputs_unchanged'] = inventory() == inputs
        report['passed'] = report['passed'] and report['inputs_unchanged']
        if server is not None:
            (root / 'model-requests.json').write_text(json.dumps(server.requests, indent=2) + '\n')
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        assert report['inputs_unchanged']


if __name__ == '__main__':
    main()
