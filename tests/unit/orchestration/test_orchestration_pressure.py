"""Pressure acceptance uses actual canonical ownership and bounded effects."""
# ruff: noqa: F401,F811

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_agent_task_requests import bound_requester, idle_requester, managed_pipeline

from flycode.domain.agent_requests import AgentTaskRequest
from flycode.domain.orchestration import BudgetAmount
from flycode.llm.catalogue import ModelCatalogue
from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.agent_requests import AgentRequests
from flycode.orchestration.dispatch import Dispatcher


def test_two_distinct_agents_pull_one_bound_task_once(bound_requester):
    p, recipe, root = bound_requester
    store, owner, runtime, workflow, project, first_instance = p
    second_instance = store.register_instance(runtime, first_instance.profile_id)
    instances = first_instance, second_instance
    issued = [AgentRequests(store).issue(owner, project, instance_id=instance.id,
        expected_revision=instance.revision, key='pressure-context-' + instance.id) for instance in instances]
    before = store.budget(owner, workflow.id)
    gate = Barrier(2)

    def ask(number):
        connection = store.host.connect(catalogue=ModelCatalogue(store.catalogue.base_url, database=store.path))
        try:
            service = AgentTaskRequests(connection)
            instance, credential = instances[number], issued[number]
            request = AgentTaskRequest(expected_instance_revision=instance.revision,
                capabilities=('python',), idempotency_key='distinct-agent-same-key')
            gate.wait(timeout=5)
            pending = service.submit(instance.id, credential.credential, request)
            selected = Dispatcher(connection, root).select(runtime,
                key='pressure-admission-' + instance.id, agent_request_id=pending.id)
            receipt = service.get(instance.id, credential.credential, pending.id)
            assert service.submit(instance.id, credential.credential, request) == receipt
            return receipt, selected
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ask, range(2)))
    assigned = [receipt for receipt, _ in results if receipt.state == 'assigned']
    refused = [receipt for receipt, _ in results if receipt.state == 'no_eligible_task']
    assert len(assigned) == len(refused) == 1
    assert assigned[0].instance_id != refused[0].instance_id
    assert assigned[0].result.attempt.task_id == recipe.writer_id
    assert assigned[0].result.attempt.execution_binding_id == recipe.worker_binding_id
    assert 'dispatch_admission_pending' in refused[0].result.reasons
    attempts = store.attempts(owner, workflow.id)
    assert len(attempts) == 1 and attempts[0].id == assigned[0].result.attempt.id
    ledger = store.budget(owner, workflow.id)
    assert ledger.spent == before.spent == BudgetAmount()
    assert ledger.reserved == attempts[0].reserved
    assert store.db.execute('SELECT count(*) FROM reservations').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM agent_task_requests').fetchone()[0] == 2
    assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0
    assigned_events = [event for event in store.events(owner, workflow.id) if event.kind == 'attempt_assigned']
    assert len(assigned_events) == 1 and assigned_events[0].attempt_id == attempts[0].id


def test_four_native_workers_hold_slots_until_confirmed_exit_before_fifth_pull(managed_pipeline, model_server, monkeypatch):
    """A fifth admission must wait until one of four real held workers exits."""
    import json
    import time
    from threading import Event

    from test_managed_coordinator import until
    from test_store import command, draft

    from flycode.domain.orchestration import AgentProfileDraft, ExecutionPermission
    from flycode.orchestration.coordinator import Coordinator
    from flycode.orchestration.host import Host
    from flycode.orchestration.projects import Projects
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.tools import Tools
    from flycode.verification.custody import stopped

    observer, owner, runtime, workflow, tasks, source, host_root, root, *_ = managed_pipeline
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    gates = (source.parent / 'pressure-gates').resolve()
    gates.mkdir()
    names = ('alpha', 'bravo', 'charlie', 'delta', 'echo')
    test_file = source / 'tests/test_pressure_gate.py'
    body = 'from pathlib import Path\nimport time\n'
    for name in names:
        body += (f'\ndef test_{name}():\n'
            f'    gate = Path({str(gates)!r})\n'
            f'    (gate / "{name}-started").write_text("started")\n'
            '    deadline = time.monotonic() + 60\n'
            f'    while not (gate / "{name}-release").exists():\n'
            '        assert time.monotonic() < deadline, "Owned gate was not released"\n'
            '        time.sleep(.02)\n')
    test_file.write_text(body)
    source_bytes = {str(path.relative_to(source)): path.read_bytes()
        for path in source.rglob('*') if path.is_file()}
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    model_ref = observer._profile(observer.instances(runtime)[0].profile_id).model_ref

    def respond(request):
        text = json.dumps(request['messages'])
        matches = [name for name in names if 'Hold pressure slot ' + name in text]
        assert len(matches) == 1, text
        return model_server.completion(json.dumps({'candidates': [{'tool': 'run_tests',
            'arguments': {'target': 'tests/test_pressure_gate.py::test_' + matches[0], 'timeout': 60}}]}))

    model_server.respond = respond
    with Host(observer.path, host_root) as leader:
        store = leader.connect(catalogue=observer.catalogue)
        try:
            project = Projects(store).resolve(source)
            queue = []
            for name in names:
                profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model_ref,
                    instructions='Run only the approved local check. Do not change source.', capabilities=('python',),
                    project_ids=(project.id,), permissions=permission, context_limit=8192))
                instance = store.register_instance(runtime, profile.id)
                task = store.create_task(owner, workflow.id, draft(permission, parent_id=tasks[0].id,
                    title='Pressure ' + name, objective='Hold pressure slot ' + name + ' using its registered local test.',
                    allowed_profile_ids=(profile.id,), budget=BudgetAmount(model_calls=2, tool_calls=6)), key='slot-' + name)
                command(store, owner, task, 'enqueue')
                queue.append((task, instance))
            for name, (task, _) in zip(names, queue):
                current = store.task(owner, task.id)
                Dispatcher(store, root).bind(owner, task.id, source, expected_revision=current.revision,
                    key='bind-slot-' + name, brain='heuristic_fsm')
            command(store, owner, tasks[0], 'enqueue')
            store.start_next(owner, key='pressure-start')
            stop = Event()
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(Coordinator(leader).run, stop.is_set)
                try:
                    until(lambda: sum((gates / (name + '-started')).exists() for name in names) == 4, timeout=35)
                    attempts = store.attempts(owner, workflow.id)
                    assert len(attempts) == 4 and all(a.state == 'running' for a in attempts)
                    records = [Runtime(store, root).worker(owner, a.id) for a in attempts]
                    assert len({record.pid for record in records}) == 4
                    assert all(record.state == 'running' and not record.stop_confirmed for record in records)
                    assert len([call for call in Tools(store).calls(owner, workflow.id) if call.status == 'running']) == 4
                    last = next(item for item in queue if item[0].id not in {a.task_id for a in attempts})
                    task, instance = last
                    credential = AgentRequests(store).issue(owner, project, instance_id=instance.id,
                        expected_revision=instance.revision, key='full-pool-fifth-context')
                    pending = AgentTaskRequests(store).submit(instance.id, credential.credential,
                        AgentTaskRequest(expected_instance_revision=instance.revision, capabilities=('python',),
                            preferred_task_id=task.id, expected_task_revision=store.task(owner, task.id).revision,
                            idempotency_key='full-pool-fifth-request'))
                    denied = Dispatcher(store, root).select(runtime, key='inspect-full-pool',
                        agent_request_id=pending.id, finalize_request=False)
                    assert denied.attempt is None and 'instance_limit' in denied.reasons
                    assert len(store.attempts(owner, workflow.id)) == 4
                    assert AgentTaskRequests(store).get(instance.id, credential.credential, pending.id).state == 'queued'
                    deadline = store.workflow(owner, workflow.id).deadline_at
                    assert deadline is not None
                    before = store.budget(owner, workflow.id)
                    time.sleep(.7)
                    assert len(store.attempts(owner, workflow.id)) == 4
                    assert store.budget(owner, workflow.id).spent == before.spent
                    first = attempts[0]
                    first_name = next(name for name, entry in zip(names, queue) if entry[0].id == first.task_id)
                    (gates / (first_name + '-release')).write_text('release')
                    until(lambda: len(store.attempts(owner, workflow.id)) == 5, timeout=20)
                    until(lambda: store._attempt(first.id).stop_confirmed, timeout=10)
                    record = Runtime(store, root).worker(owner, first.id)
                    with stopped(record.process) as receipt:
                        assert receipt.stop_confirmed
                    fifth = next(a for a in store.attempts(owner, workflow.id) if a.task_id == task.id)
                    events = store.events(owner, workflow.id)
                    stopped_index = next(i for i, event in enumerate(events)
                        if event.kind == 'worker_stopped' and event.attempt_id == first.id)
                    assigned_index = next(i for i, event in enumerate(events)
                        if event.kind == 'attempt_assigned' and event.attempt_id == fifth.id)
                    assert stopped_index < assigned_index
                    fifth_name = next(name for name, entry in zip(names, queue) if entry[0].id == task.id)
                    for name in names:
                        (gates / (name + '-release')).write_text('release')
                    until(lambda: (gates / (fifth_name + '-started')).exists(), timeout=15)
                    until(lambda: all(a.stop_confirmed for a in store.attempts(owner, workflow.id)), timeout=30)
                    assert len(store.attempts(owner, workflow.id)) == 5
                    assert store.workflow(owner, workflow.id).deadline_at == deadline
                    assert store.workflow(owner, workflow.id).state != 'succeeded'
                    budget = store.budget(owner, workflow.id)
                    assert budget.reserved == BudgetAmount() and not budget.uncertain
                    assert 4 <= budget.spent.model_calls <= 10 and 5 <= budget.spent.tool_calls <= 30
                    for attempt in store.attempts(owner, workflow.id):
                        record = Runtime(store, root).worker(owner, attempt.id)
                        with stopped(record.process) as receipt:
                            assert receipt.stop_confirmed
                    assert {str(path.relative_to(source)): path.read_bytes()
                        for path in source.rglob('*') if path.is_file()} == source_bytes
                finally:
                    for name in names:
                        (gates / (name + '-release')).write_text('release')
                    stop.set()
                    running.result(timeout=30)
        finally:
            store.close()
