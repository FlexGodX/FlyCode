"""Native four-slot pressure inside an owner-approved 64-task hierarchy."""

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
from owner_runner import REPO, counts, inventory, managed_pipeline, model_server
from saturation_scenario import NAMES, prepare
from test_managed_coordinator import until

import flycode
from flycode.domain.agent_requests import AgentTaskRequest
from flycode.domain.orchestration import BudgetAmount, TaskDraft
from flycode.domain.results import ReviewRecord
from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.agent_requests import AgentRequests
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.coordinator import Coordinator
from flycode.orchestration.host import Host
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.projects import Projects
from flycode.orchestration.runtime import Runtime
from flycode.orchestration.store import StoreError
from flycode.orchestration.tools import Tools
from flycode.verification.custody import stopped


def capacity(p, report):
    store, owner, runtime, workflow = (p[k] for k in ('store', 'owner', 'runtime', 'workflow'))
    gates, dispatcher, stop = p['gates'], p['dispatcher'], Event()
    before_source = {str(path.relative_to(p['source'])): path.read_bytes()
        for path in p['source'].rglob('*') if path.is_file()}
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(Coordinator(store.host).run, stop.is_set)
        try:
            until(lambda: sum((gates / (name + '-started')).exists() for name in NAMES[:5]) == 4, timeout=50)
            active = [a for a in store.attempts(owner, workflow.id) if a.state == 'running']
            assert len(active) == 4
            workers = [Runtime(store, dispatcher.root).worker(owner, a.id) for a in active]
            assert len({worker.pid for worker in workers}) == 4
            assert all(worker.state == 'running' and not worker.stop_confirmed for worker in workers)
            assert store.db.execute('SELECT count(*) FROM workflows WHERE slot=1').fetchone()[0] == 1
            current = store.workflow(owner, workflow.id)
            assert current.budget.model_dump() == p['recipe'].workflow_budget.model_dump()
            deadline = current.deadline_at
            assert deadline == current.started_at + 1800
            arguments = {'changes': {p['children']['parser'].id: {'objective': 'Owner requested graph revision under live pressure.'}},
                'expected_revision': current.revision,
                'expected_revisions': {task.id: task.revision for task in store.tasks(owner, workflow.id)},
                'profile_pins': p['profile_pins'], 'key': 'pressure64-live-replan'}
            before = store.budget(owner, workflow.id)
            for _ in range(2):
                with pytest.raises(StoreError) as error:
                    Pipelines(store, dispatcher.root).replan(owner, p['recipe'].id, **arguments)
                assert error.value.code == 'stop_unconfirmed'
            assert store.budget(owner, workflow.id) == before
            selected = {a.task_id for a in active}
            name, task = next((name, p['children'][name]) for name in NAMES[:5]
                if p['children'][name].id not in selected)
            instance = next(store.instance(runtime, item.id) for item in p['instances']
                if store.instance(runtime, item.id).state == 'idle')
            project = Projects(store).resolve(p['source'])
            credential = AgentRequests(store).issue(owner, project, instance_id=instance.id,
                expected_revision=instance.revision, key='pressure64-fifth-context')
            ask = AgentTaskRequest(expected_instance_revision=instance.revision, capabilities=('python',),
                preferred_task_id=task.id, expected_task_revision=store.task(owner, task.id).revision,
                idempotency_key='pressure64-fifth-request')
            pending = AgentTaskRequests(store).submit(instance.id, credential.credential, ask)
            denied = dispatcher.select(runtime, key='pressure64-full-pool-observation',
                agent_request_id=pending.id, finalize_request=False)
            assert denied.attempt is None and 'instance_limit' in denied.reasons
            assert AgentTaskRequests(store).get(instance.id, credential.credential, pending.id).state == 'queued'
            first = active[0]
            first_name = next(name for name in NAMES[:5] if p['children'][name].id == first.task_id)
            (gates / (first_name + '-release')).touch()
            until(lambda: (gates / (name + '-started')).exists(), timeout=30)
            assert store._attempt(first.id).stop_confirmed
            with stopped(Runtime(store, dispatcher.root).worker(owner, first.id).process) as receipt:
                assert receipt.stop_confirmed
            fifth = next(a for a in store.attempts(owner, workflow.id) if a.task_id == task.id)
            events = store.events(owner, workflow.id)
            stopped_index = next(n for n, event in enumerate(events) if event.kind == 'worker_stopped' and event.attempt_id == first.id)
            assigned_index = next(n for n, event in enumerate(events) if event.kind == 'attempt_assigned' and event.attempt_id == fifth.id)
            assert stopped_index < assigned_index
            for item in NAMES[:5]:
                (gates / (item + '-release')).touch()
            until(lambda: store.task(owner, p['parent'].id).blocked_reason == 'child_review_failed', timeout=90)
            until(lambda: all(store.task(owner, p['children'][name].id).completion_id for name in NAMES[1:5]), timeout=90)
            until(lambda: all(a.stop_confirmed for a in store.attempts(owner, workflow.id)), timeout=30)
            attempts = store.attempts(owner, workflow.id)
            assert len(attempts) == 11
            assert len(store.tasks(owner, workflow.id)) == 64
            assert store.workflow(owner, workflow.id).deadline_at == deadline
            assert store.workflow(owner, workflow.id).state != 'succeeded'
            assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
            ledger = store.budget(owner, workflow.id)
            assert ledger.reserved == BudgetAmount() and not ledger.uncertain
            assert ledger.spent.model_calls <= 48 and ledger.spent.tool_calls <= 120
            negative = [ReviewRecord.model_validate_json(row[0]) for row in store.db.execute('SELECT data FROM reviews')]
            assert len(negative) == 5 and sum(review.status == 'changes_requested' for review in negative) == 1
            rejected, = [review for review in negative if review.status == 'changes_requested']
            assert rejected.verdict.checks == {'baseline': 'passed', 'target': 'failed',
                'regression': 'inconclusive', 'integration': 'inconclusive'}
            failed = next(a for a in attempts if a.task_id == p['children']['parser'].id)
            artifact = Artifacts(store, dispatcher.root).for_attempt(owner, failed.id)
            assert (Artifacts(store, dispatcher.root).directory / artifact.id / 'target/src/parser.py').read_text() == 'value = -1\n'
            receipts, checks = [], []
            for attempt in [a for a in attempts if a.profile_snapshot.role == 'developer']:
                worker = Runtime(store, dispatcher.root).worker(owner, attempt.id)
                with stopped(worker.process) as receipt:
                    assert receipt.stop_confirmed
                    receipts.append({'attempt_id': attempt.id, 'stop_confirmed': True, 'reason': receipt.reason})
            for call in Tools(store).calls(owner, workflow.id):
                if call.process is not None:
                    with stopped(call.process) as receipt:
                        assert receipt.stop_confirmed
                        checks.append({'call_id': call.id, 'stop_confirmed': True, 'reason': receipt.reason})
            assert len(receipts) == 6 and len(checks) == 18
            assert {str(path.relative_to(p['source'])): path.read_bytes()
                for path in p['source'].rglob('*') if path.is_file()} == before_source
            report.update(passed=True, tasks=64, branches=28, groups=7, four_live_workers=True,
                fifth_after_confirmed_stop=True, live_replan_refusals=2,
                budget=ledger.model_dump(mode='json'), attempts=len(attempts), deadline=deadline,
                stopped_workers=receipts, registered_check_receipts=checks, negative_review_preserved=True,
                scope='64-task capacity, live replan refusal, failed independent review; no question/retry, saturation or completion claim')
        finally:
            for name in NAMES[:5]:
                (gates / (name + '-release')).touch()
            stop.set()
            running.result(timeout=45)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    assert not root.is_relative_to(REPO)
    expected = Path(os.environ.get('FLYCODE_EXPECTED_PACKAGE', str(REPO / 'src')))
    assert Path(flycode.__file__).is_relative_to(expected)
    root.mkdir(parents=True, exist_ok=False)
    inputs, started = inventory(), time.monotonic()
    report = {'passed': False, 'prepared': False, 'prepare_only': args.prepare_only}
    (root / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    server = None
    try:
        with ExitStack() as stack:
            patch = stack.enter_context(pytest.MonkeyPatch.context())
            patch.setenv('FLYCODE_ARTIFACT_ROOT', str(root / 'empty-artifacts'))
            server = stack.enter_context(contextmanager(model_server.__wrapped__)())
            server.contexts = {name: 16384 for name in server.models}
            managed = stack.enter_context(contextmanager(managed_pipeline.__wrapped__)(root, server, patch, SimpleNamespace(param=None)))
            patch.setenv('LMSTUDIO_BASE_URL', server.url)
            leader = stack.enter_context(Host(managed[0].path, managed[6]))
            store = leader.connect(catalogue=managed[0].catalogue)
            stack.callback(store.close)
            p = prepare(managed, store, server, root)
            assert len(store.tasks(p['owner'], p['workflow'].id)) == 64
            before = counts(store)
            assert before['attempts'] == before['tool_calls'] == 0 and len(server.requests) == before['model_calls'] == 1
            assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0
            # Verify the64/65 boundary against a real otherwise valid task.
            with pytest.raises(StoreError) as error:
                store.create_task(p['owner'], p['workflow'].id,
                    TaskDraft.model_validate({**p['writers'][1].model_dump(include=set(TaskDraft.model_fields)),
                        'parent_id': p['groups'][6].id}), key='pressure64-refused-65')
            assert error.value.code == 'task_limit'
            assert counts(store) == before
            assert 'managed-host:' + store.host.session.id == leader._scope(store).actor_id
            report.update(prepared=True, database=str(store.path), before=before, tasks=64,
                branches=len(p['recipe'].branches), structural_groups=len(p['groups']), configured_context=16384,
                dormant_fixture_workflows=1, eligible_developer_instances=5,
                registered_instances=len(store.instances(p['runtime'])), active_instance_limit=4)
            if not args.prepare_only:
                capacity(p, report)
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
