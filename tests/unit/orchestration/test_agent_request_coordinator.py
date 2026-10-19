"""Pull requests enter the managed coordinator's existing stage pool."""
# ruff: noqa: F401,F811

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_agent_task_requests import (
    bound_requester,
    idle_requester,
    issue,
    managed_pipeline,
    payload,
)
from test_managed_coordinator import until

from flycode.domain.orchestration import BudgetAmount
from flycode.orchestration.agent_admission import AgentTaskRequests
from flycode.orchestration.coordinator import Coordinator
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.host import Host
from flycode.orchestration.integration import Integration


def test_coordinator_selects_pending_request_through_bound_admission(bound_requester, model_server, monkeypatch):
    p, _, root = bound_requester
    store, owner, _, workflow, _, instance = p
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    issued = issue(p)
    service = AgentTaskRequests(store)
    pending = service.submit(instance.id, issued.credential, payload(p))
    _, selected = Coordinator(store.host)._select(None)
    receipt = service.get(instance.id, issued.credential, pending.id)
    assert selected is not None and receipt.state == 'assigned'
    assert selected[0] == str(root) and selected[2:] == (receipt.result.attempt.id, 'agent')
    assert selected[1] == service.scope(issued.context)
    assert receipt.result.attempt.execution_binding_id is not None
    assert len(store.attempts(owner, workflow.id)) == 1
    events = [json.loads(row[0]) for row in store.db.execute('SELECT data FROM events')]
    assert any(event['kind'] == 'worker_dispatched' and event['data'].get('agent_request_id') == pending.id
        for event in events)


def test_request_and_context_changes_wake_idle_coordinator(idle_requester):
    p = idle_requester
    store = p[0]
    coordinator = Coordinator(store.host)
    scope = coordinator._scope(store, current=True)
    before, _ = coordinator._inputs(store, scope)
    issued = issue(p)
    after_issue, _ = coordinator._inputs(store, scope)
    assert after_issue != before
    AgentTaskRequests(store).submit(p[-1].id, issued.credential, payload(p))
    after_submit, _ = coordinator._inputs(store, scope)
    assert after_submit != after_issue


def test_replaced_context_closes_pending_request_without_assignment(idle_requester, model_server, monkeypatch):
    p = idle_requester
    store, owner, _, workflow, _, instance = p
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    issued = issue(p)
    service = AgentTaskRequests(store)
    pending = service.submit(instance.id, issued.credential, payload(p))
    issue(p, key='replace-request-authority')
    before = store.budget(owner, workflow.id)
    _, selected = Coordinator(store.host)._select(None)
    row = store.db.execute('SELECT state,data FROM agent_task_requests WHERE id=?', (pending.id,)).fetchone()
    assert selected is None and row['state'] == 'denied'
    assert json.loads(row['data'])['error_code'] == 'agent_identity'
    assert not service.pending() and not store.attempts(owner, workflow.id)
    assert store.budget(owner, workflow.id) == before


def test_real_host_restart_closes_old_pending_gate_and_preserves_raw_audit(managed_pipeline, model_server, monkeypatch):
    from flycode.orchestration.agent_requests import AgentRequests
    from flycode.orchestration.projects import Projects

    store, owner, runtime, workflow, _, source, host_root, *_ = managed_pipeline
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        try:
            instance = connection.instances(runtime)[0]
            issued = AgentRequests(connection).issue(owner, Projects(connection).resolve(source),
                instance_id=instance.id, expected_revision=instance.revision, key='before-restart')
            from flycode.domain.agent_requests import AgentTaskRequest

            pending = AgentTaskRequests(connection).submit(instance.id, issued.credential,
                AgentTaskRequest(expected_instance_revision=instance.revision, idempotency_key='before-restart'))
            raw = connection.db.execute('SELECT data FROM agent_task_requests WHERE id=?', (pending.id,)).fetchone()[0]
        finally:
            connection.close()
    with Host(store.path, host_root) as leader:
        coordinator = Coordinator(leader)
        _, selected = coordinator._select(None)
        assert selected is None
        row = store.db.execute('SELECT state,data FROM agent_task_requests WHERE id=?', (pending.id,)).fetchone()
        assert row['state'] == 'denied' and row['data'] == raw
        assert not store.attempts(owner, workflow.id)


def test_assignment_event_and_request_result_roll_back_together(bound_requester, monkeypatch):
    p, _, root = bound_requester
    store, owner, runtime, workflow, _, instance = p
    issued = issue(p)
    service = AgentTaskRequests(store)
    pending = service.submit(instance.id, issued.credential, payload(p))
    before = store.budget(owner, workflow.id)
    original = store._event

    def fail_after_outbox(*args, **kwargs):
        original(*args, **kwargs)
        if args[2] == 'worker_dispatched':
            raise OSError('outbox publication failed')

    monkeypatch.setattr(store, '_event', fail_after_outbox)
    with pytest.raises(OSError, match='outbox publication failed'):
        Dispatcher(store, root).select(runtime, key='atomic-request', agent_request_id=pending.id)
    assert service.get(instance.id, issued.credential, pending.id) == pending
    assert not store.attempts(owner, workflow.id) and store.budget(owner, workflow.id) == before
    assert not store.db.execute("SELECT 1 FROM events WHERE json_extract(data,'$.kind')='worker_dispatched'").fetchone()


def test_request_runs_real_worker_review_and_integration_in_common_pool(bound_requester, model_server, monkeypatch):
    p, _, root = bound_requester
    store, owner, _, workflow, project, instance = p
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    issued = issue(p)
    service = AgentTaskRequests(store)
    pending = service.submit(instance.id, issued.credential, payload(p))
    stop = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(Coordinator(store.host).run, stop.is_set)
        try:
            until(lambda: service.get(instance.id, issued.credential, pending.id).state == 'assigned', timeout=15)
            until(lambda: store.workflow(owner, workflow.id).deadline_at is not None, timeout=15)
            deadline = store.workflow(owner, workflow.id).deadline_at
            assert deadline is not None
            until(lambda: store.workflow(owner, workflow.id).state == 'succeeded', timeout=45)
            receipt = service.get(instance.id, issued.credential, pending.id)
            assert receipt.state == 'assigned' and receipt.result.attempt.instance_id == instance.id
            assert service.submit(instance.id, issued.credential, payload(p)) == receipt
            attempts = store.attempts(owner, workflow.id)
            assert len(attempts) == 3 and all(attempt.stop_confirmed for attempt in attempts)
            assert receipt.result.attempt.id in {attempt.id for attempt in attempts}
            identity = store.db.execute('SELECT id FROM integrations').fetchone()[0]
            assert Integration(store, root).current(owner, identity)
            budget = store.budget(owner, workflow.id)
            assert budget.reserved == BudgetAmount() and not budget.uncertain
            assert store.workflow(owner, workflow.id).deadline_at == deadline
            from pathlib import Path

            source = Path(project.root.path)
            assert (source / 'src/parser.py').read_text() == 'value = 1\n'
            assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        finally:
            stop.set()
            running.result(timeout=30)


def test_request_uses_actual_approved_runtime_root(idle_requester, managed_pipeline, model_server, tmp_path, monkeypatch):
    from flycode.orchestration.pipeline import Pipelines
    from flycode.verification.engine import VerificationEngine

    p = idle_requester
    store, owner, runtime, workflow, _, instance = p
    _, _, _, _, tasks, source, _, _, oracle, checks, _ = managed_pipeline
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    root = (tmp_path / 'separate-approved-runtime').resolve()
    dispatcher = Dispatcher(store, root)
    writer = tasks[1]
    previous = Dispatcher(store, managed_pipeline[7]).binding(owner, writer.id)
    dispatcher.bind(owner, writer.id, source, expected_revision=writer.revision,
        expected_binding_id=previous.id, key='separate-runtime', brain='heuristic_fsm')
    approval = VerificationEngine(root / 'verification').oracles.approve(tasks[0].acceptance, checks, oracle)
    Pipelines(store, root).approve(owner, workflow.id, writer.id, tasks[2].id, tasks[3].id,
        source=source, expected_revisions={item.id: item.revision for item in tasks},
        approval_hashes={item.id: approval for item in tasks}, key='separate-approval')
    issued = issue(p)
    service = AgentTaskRequests(store)
    pending = service.submit(instance.id, issued.credential, payload(p))
    _, selected = Coordinator(store.host)._select(None)
    assert selected is not None and selected[0] == str(root)
    receipt = service.get(instance.id, issued.credential, pending.id)
    assert receipt.state == 'assigned' and selected[2] == receipt.result.attempt.id
    assert len(store.attempts(runtime, workflow.id)) == 1
