"""Automatic sibling exchange, negative retry and quiescent owner graph revision."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from message_scenario import configure
from owner_runner import (
    REPO,
    counts,
    inventory,
    live_parent,
    managed_pipeline,
    model_server,
    parent_ready,
    pinned_parent,
    pipeline_ready,
)
from test_managed_coordinator import until

import flycode
from flycode.domain.agent_requests import AgentTaskRequest
from flycode.domain.message_routes import MessageRouteEdge
from flycode.domain.orchestration import AgentProfileDraft, BudgetAmount, TaskCommand
from flycode.domain.results import ReviewRecord
from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.agent_requests import AgentRequests
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.coordinator import Coordinator
from flycode.orchestration.integration import Integration
from flycode.orchestration.message_routes import MessageRoutes
from flycode.orchestration.messages import Messages
from flycode.orchestration.owner_requests import OwnerMessageRoutes
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import StoreError

REPAIR = 'Owner graph revision repair: set parser value to 1 under the unchanged criteria.'


def prepare(p, server, root):
    store, owner = p['store'], p['owner']
    task = store.task(owner, p['child'].id)
    previous = store._profile(task.allowed_profile_ids[0])
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **previous.model_dump(include=set(AgentProfileDraft.model_fields)), 'context_limit': 16384}))
    p['child'] = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='pressure-configured-context', changes={'allowed_profile_ids': (profile.id,)}))
    scenario = configure(p, server, root)
    for name, budget in [('child', BudgetAmount(model_calls=12, tool_calls=12)),
                         ('child_review', BudgetAmount(tool_calls=12))]:
        task = store.task(owner, p[name].id)
        p[name] = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='pressure-lifetime-' + name, changes={'budget': budget.model_dump()}))
    current = store.task(owner, p['integrator'].id)
    if current.state == 'blocked':
        store.command(owner, current.id, TaskCommand(operation='reopen', expected_revision=current.revision,
            idempotency_key='pressure-integrator-reopen'))
    p = pinned_parent.__wrapped__(p)
    dispatcher = p['dispatcher']
    for name in ('parent', 'child', 'second'):
        task = store.task(owner, p[name].id)
        try:
            previous = dispatcher.binding(owner, task.id).id
        except StoreError as error:
            assert error.code == 'not_found'
            previous = None
        dispatcher.bind(owner, task.id, p['source'], expected_revision=task.revision,
            expected_binding_id=previous, key='pressure-final-binding-' + name, brain='heuristic_fsm')
    pairs = {p['parent'].id: p['parent_review'].id, p['child'].id: p['child_review'].id,
        p['second'].id: p['second_review'].id}
    recipe = Pipelines(store, dispatcher.root).approve_parent(owner, p['workflow'].id, pairs, p['integrator'].id,
        source=p['source'], expected_revisions={task.id: task.revision for task in store.tasks(owner, p['workflow'].id)},
        approval_hashes=p['approvals'], profile_pins=p['profile_pins'], key='pressure-parent-authority',
        retry_policies={p['child'].id: {'max_retries': 1, 'writer_budget': {'model_calls': 4, 'tool_calls': 4},
            'reviewer_budget': {'model_calls': 0, 'tool_calls': 4}}})
    project = Projects(store).resolve(p['source'])
    grant = MessageRoutes(store).approve(owner, project, OwnerMessageRoutes(operation='message_routes',
        project_id=project.id, project_revision=project.revision, pipeline_id=recipe.id,
        expected_revision=store.workflow(owner, recipe.workflow_id).revision,
        task_revisions={task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)},
        expected_grant_id=None, edges=(MessageRouteEdge(sender_task_id=p['child'].id, recipient_task_id=p['second'].id),
            MessageRouteEdge(sender_task_id=p['second'].id, recipient_task_id=p['child'].id)), idempotency_key='pressure-routes'))
    original = server.respond
    retry_calls, revised_calls = [], []

    def response(request):
        prompt = request['messages'][-1]['content']
        if REPAIR in prompt:
            revised_calls.append(request)
            assert len(revised_calls) <= 4
            action = ({'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = 0', 'new_text': 'value = 1'}} if len(revised_calls) == 1 else {'tool': 'stop', 'arguments': {}})
        elif p['child'].objective in prompt and scenario['calls']['a'] >= 4:
            retry_calls.append(request)
            assert len(retry_calls) <= 4
            assert 'changes_requested' in prompt and 'value = -1' in prompt
            assert 'tests/test_parser.py::test_parse' in prompt
            action = ({'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = -1', 'new_text': 'value = -2'}} if len(retry_calls) == 1 else {'tool': 'stop', 'arguments': {}})
        elif p['child'].objective in prompt and scenario['calls']['a'] == 3:
            scenario['calls']['a'] += 1
            action = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = -1'}}
        else:
            return original(request)
        scenario['observed'].append({'kind': 'pressure_model_action', 'at': time.time(), 'action': action})
        return server.completion(json.dumps({'candidates': [action]}))

    server.respond = response
    assert len(store.tasks(owner, recipe.workflow_id)) == 7
    assert store.db.execute('SELECT count(*) FROM instances').fetchone()[0] == 4
    assert not store.attempts(owner, recipe.workflow_id)
    for name in ('parent', 'child', 'second'):
        current = store.task(owner, p[name].id)
        profile = store._profile(current.allowed_profile_ids[0])
        assert profile.context_limit == profile.context_capacity == 16384
    return p, recipe, project, grant, scenario, retry_calls, revised_calls


def revision_arguments(p, recipe, key):
    store, owner = p['store'], p['owner']
    return {'changes': {p['child'].id: {'objective': REPAIR}},
        'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)},
        'profile_pins': p['profile_pins'], 'key': key}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert Path(flycode.__file__).is_relative_to(expected), flycode.__file__
    root.mkdir(parents=True, exist_ok=False)
    inputs, started = inventory(), time.monotonic()
    report = {'passed': False, 'prepared': False, 'root': str(root), 'prepare_only': args.prepare_only}
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    server = None
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            server.contexts = {name: 16384 for name in server.models}
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(root, server, patch, SimpleNamespace(param=None)))
            initial = parent_ready.__wrapped__(pipeline_ready.__wrapped__(managed), server)
            p = stack.enter_context(contextmanager(live_parent.__wrapped__)(initial, managed, patch, SimpleNamespace(param=None)))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            p, recipe, project, grant, scenario, retry_calls, revised_calls = prepare(p, server, root)
            store, owner, runtime, workflow = (p[key] for key in ('store', 'owner', 'host', 'workflow'))
            report.update(prepared=True, database=str(store.path), before=counts(store), configured_context=16384)
            host_actor = 'managed-host:' + store.host.session.id
            if args.prepare_only:
                assert len(server.requests) == report['before']['model_calls'] == 1
                assert report['before']['attempts'] == report['before']['tool_calls'] == 0
                assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0
                return
            gates, stop = scenario['gates'], Event()
            original_pipeline = store.db.execute('SELECT data FROM pipelines WHERE id=?', (recipe.id,)).fetchone()[0]
            source_before = {str(path.relative_to(p['source'])): path.read_bytes()
                for path in p['source'].rglob('*') if path.is_file()}
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(Coordinator(store.host).run, stop.is_set)
                try:
                    until(lambda: (gates / 'a1-started').exists() and (gates / 'boot-started').exists(), timeout=35)
                    deadline = store.workflow(owner, workflow.id).deadline_at
                    report['deadline'] = deadline
                    before = store.budget(owner, workflow.id)
                    arguments = revision_arguments(p, recipe, 'pressure-live-replan')
                    refusals = []
                    for _ in range(2):
                        try:
                            Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **arguments)
                            pytest.fail('Live workers allowed owner graph revision')
                        except StoreError as error:
                            assert error.code == 'stop_unconfirmed'
                            refusals.append(error.code)
                    assert store.budget(owner, workflow.id) == before
                    idle = next(instance for instance in store.instances(runtime) if instance.state == 'idle'
                        and store._profile(instance.profile_id).role == 'developer')
                    issued = AgentRequests(store).issue(owner, project, instance_id=idle.id,
                        expected_revision=idle.revision, key='pressure-idle-requester')
                    ask = AgentTaskRequest(expected_instance_revision=idle.revision, capabilities=('python',),
                        idempotency_key='pressure-no-recursive-work')
                    requested = AgentTaskRequests(store).submit(idle.id, issued.credential, ask)
                    until(lambda: AgentTaskRequests(store).get(idle.id, issued.credential, requested.id).state != 'queued', timeout=10)
                    assert AgentTaskRequests(store).get(idle.id, issued.credential, requested.id).state == 'no_eligible_task'
                    (gates / 'boot-release').touch()
                    until(lambda: (gates / 'b-started').exists(), timeout=15)
                    questions = [m for m in Messages(store).journal(owner, workflow.id) if m.content.kind == 'question']
                    assert len(questions) == 1
                    (gates / 'a1-release').touch()
                    until(lambda: (gates / 'a2-started').exists(), timeout=15)
                    answers = [m for m in Messages(store).journal(owner, workflow.id) if m.content.kind == 'answer']
                    assert len(answers) == 1 and answers[0].causation_id == questions[0].id
                    (gates / 'b-release').touch()
                    until(lambda: (gates / 'answer-consumed').exists(), timeout=15)
                    assert (gates / 'answer-consumed').read_text() == answers[0].id

                    def accepted_answer():
                        row = store.db.execute('SELECT data FROM message_inbox WHERE message_id=?', (answers[0].id,)).fetchone()
                        return row is not None and json.loads(row['data'])['outcome'] == 'received'

                    until(accepted_answer, timeout=10)
                    (gates / 'a2-release').touch()
                    def exhausted():
                        current = store.task(owner, p['child'].id)
                        assert current.state != 'failed' or current.blocked_reason is not None, 'Writer failed before retry-exhaustion receipt'
                        return current.blocked_reason == 'retry_exhausted'

                    until(exhausted, timeout=65)
                    until(lambda: all(a.stop_confirmed for a in store.attempts(owner, workflow.id)), timeout=30)
                    until(lambda: store.task(owner, p['parent'].id).blocked_reason in {'child_failed', 'child_review_failed'}, timeout=15)
                    assert store.task(owner, p['second'].id).completion_id
                    authors = [a for a in store.attempts(owner, workflow.id) if a.task_id == p['child'].id]
                    assert len(authors) == 2
                    artifacts = [Artifacts(store, p['dispatcher'].root).for_attempt(owner, a.id) for a in authors]
                    assert [(Artifacts(store, p['dispatcher'].root).directory / artifact.id / 'target/src/parser.py').read_text()
                        for artifact in artifacts] == ['value = -1\n', 'value = -2\n']
                    negative = [ReviewRecord.model_validate_json(row[0]) for row in store.db.execute('SELECT data FROM reviews')]
                    assert len([review for review in negative if review.status == 'changes_requested']) == 2
                    assert all((p['source'] / name).read_bytes() == raw for name, raw in source_before.items())
                    before = store.budget(owner, workflow.id)
                    assert before.reserved == BudgetAmount() and not before.uncertain
                    MessageRoutes(store).approve(owner, project, OwnerMessageRoutes(operation='message_routes',
                        project_id=project.id, project_revision=project.revision, pipeline_id=recipe.id,
                        expected_revision=store.workflow(owner, workflow.id).revision,
                        task_revisions={task.id: task.revision for task in store.tasks(owner, workflow.id)},
                        expected_grant_id=grant.id, edges=(), idempotency_key='pressure-retire-message-routes'))
                    previous_ids = [a.id for a in store.attempts(owner, workflow.id)]
                    previous_reservations = {row['attempt_id']: tuple(row) for row in store.db.execute('SELECT * FROM reservations')
                        if row['attempt_id'] in previous_ids}
                    arguments = revision_arguments(p, recipe, 'pressure-quiescent-replan')
                    revised = Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **arguments)
                    assert Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **arguments) == revised
                    assert revised.graph_revision == 1
                    assert {row['attempt_id']: tuple(row) for row in store.db.execute('SELECT * FROM reservations')
                        if row['attempt_id'] in previous_ids} == previous_reservations
                    assert store.db.execute('SELECT data FROM pipelines WHERE id=?', (recipe.id,)).fetchone()[0] == original_pipeline
                    assert store.db.execute("SELECT count(*) FROM pipeline_receipts WHERE pipeline_id=? AND stage='graph:1'", (recipe.id,)).fetchone()[0] == 1
                    def workflow_completed():
                        current = store.task(owner, p['integrator'].id)
                        assert current.state not in {'failed', 'cancelled'}, 'Integration terminated: ' + current.state
                        return store.workflow(owner, workflow.id).state == 'succeeded'

                    until(workflow_completed, timeout=180)
                    result_id = store.task(owner, p['parent'].id).result_id
                    assert Integration(store, p['dispatcher'].root).current(owner, result_id)
                    attempts = store.attempts(owner, workflow.id)
                    assert len(attempts) == 12 and all(a.stop_confirmed for a in attempts)
                    assert len([a for a in attempts if a.task_id == p['child'].id]) == 3
                    assert len([a for a in attempts if a.task_id == p['child_review'].id]) == 3
                    ledger = store.budget(owner, workflow.id)
                    assert ledger.reserved == BudgetAmount() and not ledger.uncertain
                    assert ledger.spent.model_calls <= 18 and ledger.spent.tool_calls <= 120
                    assert store.workflow(owner, workflow.id).deadline_at == deadline
                    source_after = {str(path.relative_to(p['source'])): path.read_bytes()
                        for path in p['source'].rglob('*') if path.is_file()}
                    assert set(source_after) == set(source_before)
                    assert sorted(name for name in source_before if source_after[name] != source_before[name]) == [
                        'src/extra.py', 'src/note.py', 'src/parser.py', 'src/second.py']
                    assert source_after['dirty.bin'] == source_before['dirty.bin']
                    assert len(store.tasks(owner, workflow.id)) == 7
                    assert len([m for m in Messages(store).journal(owner, workflow.id) if m.content.kind in {'question', 'answer'}]) == 2
                    quiet = counts(store)
                    settled = time.monotonic()
                    command_cursor = store.db.execute('SELECT max(rowid) FROM commands').fetchone()[0]
                    report['idle_samples'] = [{'at': settled, 'counts': quiet}]

                    def idle_settled():
                        nonlocal quiet, settled
                        current, now = counts(store), time.monotonic()
                        assert {k: v for k, v in current.items() if k != 'commands'} == {
                            k: v for k, v in quiet.items() if k != 'commands'}, 'New work after completion'
                        if current != quiet:
                            quiet, settled = current, now
                            report['idle_samples'].append({'at': now, 'counts': current})
                        return now - settled >= 5

                    until(idle_settled, timeout=20)
                    rows = store.db.execute('SELECT actor_id,key,result FROM commands WHERE rowid>?', (command_cursor,)).fetchall()
                    assert all(row['actor_id'] == host_actor
                        and row['key'].startswith('host-select:') and json.loads(row['result'])['attempt'] is None for row in rows)
                    report['idle_settlement_commands'] = [dict(row) for row in rows]
                    time.sleep(2)
                    assert counts(store) == quiet, 'Idle command churn after settlement'
                    assert store.workflow(owner, workflow.id).state == 'succeeded'
                    assert store.task(owner, p['parent'].id).result_id == result_id
                    assert Integration(store, p['dispatcher'].root).current(owner, result_id)
                    assert {str(path.relative_to(p['source'])): path.read_bytes()
                        for path in p['source'].rglob('*') if path.is_file()} == source_after
                    report.update(passed=True, live_replan_refusals=refusals, budget=ledger.model_dump(mode='json'),
                        after=quiet, attempts=[a.model_dump(mode='json') for a in attempts],
                        model_actions=scenario['observed'], retry_model_calls=len(retry_calls), revised_model_calls=len(revised_calls),
                        messages=[m.model_dump(mode='json') for m in Messages(store).journal(owner, workflow.id)],
                        result_id=result_id, current_integration=True, saturated_combined_limits=False)
                finally:
                    for name in ('a1', 'a2', 'boot', 'b'):
                        (gates / (name + '-release')).touch()
                    stop.set()
                    running.result(timeout=35)
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
        assert report['inputs_unchanged'], 'Measured source/tests changed during pressure run'


if __name__ == '__main__':
    main()
