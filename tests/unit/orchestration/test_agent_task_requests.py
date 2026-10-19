"""An issued idle instance requests the canonical bound dispatcher, never self-assigns."""
# ruff: noqa: F401,F811

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_agent_request_identity import idle_requester, issue, managed_pipeline

from flycode.domain.orchestration import BudgetAmount
from flycode.llm.catalogue import ModelCatalogue
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.store import StoreError


def payload(p, **changes):
    from flycode.domain.agent_requests import AgentTaskRequest

    return AgentTaskRequest(expected_instance_revision=p[-1].revision, capabilities=('python',),
        idempotency_key=changes.pop('idempotency_key', 'ask-for-task'), **changes)


@pytest.fixture
def bound_requester(idle_requester, managed_pipeline):
    p = idle_requester
    recipe = managed_pipeline[-1]()
    return p, recipe, managed_pipeline[7]


def test_request_replay_is_inert_until_common_bound_admission(bound_requester):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p, recipe, root = bound_requester
    store, owner, runtime, workflow, _, instance = p
    credential = issue(p)
    service = AgentTaskRequests(store)
    before = store.budget(owner, workflow.id)
    first = service.submit(instance.id, credential.credential, payload(p))
    assert first.state == 'queued' and first.result is None
    assert service.submit(instance.id, credential.credential, payload(p)) == first
    assert not store.attempts(owner, workflow.id) and store.budget(owner, workflow.id) == before
    result = Dispatcher(store, root).select(runtime, key='request-dispatch', agent_request_id=first.id)
    assert result.attempt is not None and result.attempt.instance_id == instance.id
    assert result.attempt.task_id == recipe.writer_id and result.attempt.execution_binding_id == recipe.worker_binding_id
    receipt = service.get(instance.id, credential.credential, first.id)
    assert receipt.state == 'assigned' and receipt.result == result
    assert service.submit(instance.id, credential.credential, payload(p)) == receipt
    assert len(store.attempts(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id).spent == BudgetAmount()
    assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0


def test_racing_duplicate_requests_commit_one_queue_record(idle_requester):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p = idle_requester
    store, _, _, _, _, instance = p
    credential = issue(p)
    barrier = Barrier(2)

    def submit(_):
        connection = store.host.connect(catalogue=ModelCatalogue(store.catalogue.base_url, database=store.path))
        try:
            barrier.wait(timeout=5)
            return AgentTaskRequests(connection).submit(instance.id, credential.credential, payload(p))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert store.db.execute('SELECT count(*) FROM agent_task_requests').fetchone()[0] == 1


def test_changed_replay_and_second_pending_key_cannot_multiply_work(idle_requester):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p = idle_requester
    store, owner, _, workflow, _, instance = p
    credential = issue(p)
    service = AgentTaskRequests(store)
    service.submit(instance.id, credential.credential, payload(p))
    with pytest.raises(StoreError, match='idempotency'):
        service.submit(instance.id, credential.credential, payload(p, available=False))
    with pytest.raises(StoreError, match='request_pending'):
        service.submit(instance.id, credential.credential, payload(p, idempotency_key='second-distinct-key'))
    assert len(service.pending()) == 1 and not store.attempts(owner, workflow.id)


@pytest.mark.parametrize('kind', ['stale', 'capabilities', 'preferred_foreign', 'credential'])
def test_untrusted_request_cannot_gain_admission(idle_requester, kind):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p = idle_requester
    store, owner, _, workflow, _, instance = p
    credential = issue(p)
    body = payload(p).model_dump()
    raw = credential.credential
    code = 'agent_context_stale'
    if kind == 'stale':
        body['expected_instance_revision'] += 1
    elif kind == 'capabilities':
        body['capabilities'] = ('unregistered-network-access',)
        code = 'capabilities'
    elif kind == 'preferred_foreign':
        body.update(preferred_task_id='f' * 32, expected_task_revision=1)
        code = 'scope'
    else:
        raw, code = 'f' * 32 + '.' + '0' * 64, 'agent_identity'
    with pytest.raises(StoreError, match=code):
        AgentTaskRequests(store).submit(instance.id, raw, body)
    assert store.db.execute('SELECT count(*) FROM agent_task_requests').fetchone()[0] == 0
    assert not store.attempts(owner, workflow.id)


@pytest.mark.parametrize('condition', ['no_queue', 'paused', 'unavailable', 'narrow_capabilities', 'source_changed'])
def test_no_eligible_request_finishes_once_with_backoff(idle_requester, managed_pipeline, condition):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p = idle_requester
    store, owner, runtime, workflow, _, instance = p
    if condition != 'no_queue':
        managed_pipeline[-1]()
    if condition == 'paused':
        store.pause_queue(owner, True, key='pause-request-queue')
    if condition == 'narrow_capabilities':
        writer = store.task(owner, managed_pipeline[4][1].id)
        # The current binding/recipe is pinned before selection; its required capability is unchanged.
        assert writer.capabilities == ('python',)
    if condition == 'source_changed':
        (managed_pipeline[5] / 'src/parser.py').write_text('value = 9\n')
    credential = issue(p)
    body = payload(p).model_dump()
    if condition == 'unavailable':
        body['available'] = False
    if condition == 'narrow_capabilities':
        body['capabilities'] = ()
    service = AgentTaskRequests(store)
    first = service.submit(instance.id, credential.credential, body)
    result = Dispatcher(store, managed_pipeline[7]).select(runtime, key='empty-request', agent_request_id=first.id)
    assert result.attempt is None
    receipt = service.get(instance.id, credential.credential, first.id)
    assert receipt.state == 'no_eligible_task' and receipt.result == result and result.retry_after_s == 30
    assert receipt.next_request_at == receipt.completed_at + 30
    with pytest.raises(StoreError, match='request_backoff'):
        service.submit(instance.id, credential.credential, {**body, 'idempotency_key': 'fresh-key-too-soon'})
    assert not service.pending() and not store.attempts(owner, workflow.id)


def test_two_dispatch_connections_cannot_assign_the_same_request(bound_requester):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p, _, root = bound_requester
    store, owner, runtime, workflow, _, instance = p
    credential = issue(p)
    first = AgentTaskRequests(store).submit(instance.id, credential.credential, payload(p))
    barrier = Barrier(2)

    def select(_):
        connection = store.host.connect(catalogue=ModelCatalogue(store.catalogue.base_url, database=store.path))
        try:
            barrier.wait(timeout=5)
            return Dispatcher(connection, root).select(runtime, key='same-agent-request', agent_request_id=first.id)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, range(2)))
    assert results[0] == results[1] and results[0].attempt is not None
    assert len(store.attempts(owner, workflow.id)) == 1


@pytest.mark.parametrize('extra', [{'role': 'runtime'}, {'reserve': {'model_calls': 60}}, {'binding_id': 'f' * 32},
    {'capabilities': ('python', 'python')}, {'available': 'true'}])
def test_closed_request_body_never_admits_caller_authority(extra):
    from pydantic import ValidationError

    from flycode.domain.agent_requests import AgentTaskRequest

    with pytest.raises(ValidationError):
        AgentTaskRequest.model_validate({'expected_instance_revision': 1, 'idempotency_key': 'typed', **extra})


def test_normal_dispatch_cannot_steal_an_instances_pending_request(bound_requester):
    from flycode.orchestration.agent_admission import AgentTaskRequests

    p, _, root = bound_requester
    store, _, runtime, _, _, instance = p
    credential = issue(p)
    service = AgentTaskRequests(store)
    first = service.submit(instance.id, credential.credential, payload(p))
    assert Dispatcher(store, root).select(runtime, key='normal-push-after-request').attempt is None
    assert service.get(instance.id, credential.credential, first.id).state == 'queued'
    result = Dispatcher(store, root).select(runtime, key='pull-owns-admission', agent_request_id=first.id)
    assert result.attempt is not None and result.attempt.instance_id == instance.id


@pytest.mark.parametrize('mode', ['tie', 'priority', 'oldest'])
def test_preferred_task_is_only_a_baseline_tie_breaker(idle_requester, mode):
    from flycode.orchestration.scheduler import ordered
    from flycode.orchestration.store import changed

    store, owner, _, workflow, _, _ = idle_requester
    base, now = store.task(owner, workflow.root_task_id), store.clock()
    first = changed(base, id='a' * 32, priority=1, queued_at=now)
    second = changed(base, id='b' * 32, priority=1, queued_at=now)
    preferred, expected, starts = second.id, second.id, 0
    if mode == 'priority':
        second = changed(second, priority=3)
        preferred = first.id
    elif mode == 'oldest':
        first = changed(first, priority=0, queued_at=now - 60)
        second = changed(second, priority=3)
        expected, starts = first.id, 7
    assert ordered([first, second], now, starts, preferred_id=preferred)[0].id == expected
