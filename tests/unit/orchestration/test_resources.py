"""Model requests acquire a durable endpoint slot before any HTTP effect."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest
from openai import APIStatusError, APITimeoutError, OpenAI

from flycode.domain.orchestration import (
    AccessScope,
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
from flycode.llm.catalogue import ModelCatalogue
from flycode.orchestration.store import Store, StoreError


@pytest.fixture
def active(tmp_path, model_server):
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store = Store(tmp_path / 'resources.sqlite', catalogue=catalogue)
    owner = AccessScope(actor_id='owner', project_ids={'project'}, role='owner')
    runtime = AccessScope(actor_id='host', project_ids={'project'}, role='runtime')
    permission = ExecutionPermission(approved=True, tools=('read_file',), read_scopes=('.',))
    acceptance = AcceptanceContract(id='contract', version=1, criteria=[AcceptanceCriterion(
        id='repair', check_ids=['regression'], expected_case_ids=['tests/test_parser.py::test_parse'])])
    draft = TaskDraft(title='Inspect parser', objective='Inspect the parser.', acceptance=acceptance,
        permissions=permission, budget=BudgetAmount(model_calls=2, tool_calls=4))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='project', task=draft,
                                    budget=WorkflowBudget()), key='workflow')
    def enqueue(task):
        return store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
                                                        idempotency_key='enqueue-' + task.id))
    enqueue(store.task(owner, workflow.root_task_id))
    tasks = [enqueue(store.create_task(owner, workflow.id,
        draft.model_copy(update={'parent_id': workflow.root_task_id}), key=f'task-{i}')) for i in range(2)]
    store.start_next(owner, key='start')
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Inspect only.', project_ids=('project',), permissions=permission, context_limit=4096))
    attempts = []
    for task in tasks:
        instance = store.register_instance(runtime, profile.id)
        attempt = store.claim(runtime, ClaimRequest(instance_id=instance.id,
            expected_instance_revision=instance.revision, task_id=task.id, expected_task_revision=task.revision,
            reserve=BudgetAmount(model_calls=2, tool_calls=4), idempotency_key='claim-' + task.id)).attempt
        attempts.append(store.confirm_started(runtime, attempt.id, attempt.fence))
    yield store, owner, runtime, catalogue, workflow, attempts, model
    store.close()


def test_two_connections_cannot_reserve_the_same_endpoint_or_double_charge(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, _ = active
    barrier = Barrier(2)
    def compete(attempt):
        connection = Store(store.path, catalogue=catalogue)
        try:
            barrier.wait(timeout=3)
            return Resources(connection).begin(runtime, attempt.id, attempt.fence, key=attempt.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, attempts))
    admitted = [result for result in results if result is not None]
    assert len(admitted) == 1 and admitted[0].fresh
    call = admitted[0].call
    replay = Resources(store).begin(runtime, call.attempt_id, call.fence, key=call.attempt_id)
    assert not replay.fresh and replay.call.id == call.id
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store.budget(owner, workflow.id).reserved.model_calls == 3
    assert sum(event.kind == 'model_start' for event in store.events(owner, workflow.id)) == 1


async def test_real_requests_are_serial_while_wait_and_stop_remain_responsive(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    entered, release, stop = Event(), Event(), Event()
    lock = Lock()
    counts = {'active': 0, 'maximum': 0}
    def response(request):
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
        entered.set()
        assert release.wait(timeout=5)
        with lock:
            counts['active'] -= 1
        return model_server.completion('[]')
    model_server.respond = response
    def invoke(attempt, stopping):
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempt.id, attempt.fence, key=attempt.id,
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the parser without editing.'}]),
                    should_stop=stopping.is_set)
        finally:
            connection.close()
    first = asyncio.create_task(asyncio.to_thread(invoke, attempts[0], Event()))
    assert await asyncio.to_thread(entered.wait, 3)
    second = asyncio.create_task(asyncio.to_thread(invoke, attempts[1], stop))
    try:
        deadline = time.monotonic() + 3
        while not any(e.kind == 'model_wait' for e in store.events(owner, workflow.id)):
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        stop.set()
        with pytest.raises(InterruptedError):
            await asyncio.wait_for(second, .5)
        assert not first.done() and len(model_server.requests) == 2  # probe + first request
        release.set()
        await asyncio.wait_for(first, 3)
        assert counts['maximum'] == 1
        ledger = store.budget(owner, workflow.id)
        assert ledger.spent.model_calls == 1 and ledger.reserved.model_calls == 3
        records = Resources(store).calls(owner, workflow.id)
        assert len(records) == 1 and records[0].status == 'completed'
        assert records[0].usage.total_tokens == 11
        await asyncio.to_thread(invoke, attempts[1], Event())
        assert len(model_server.requests) == 3 and counts['maximum'] == 1
        assert store.budget(owner, workflow.id).spent.model_calls == 2
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


def test_http_failure_is_charged_and_replay_never_sends_a_second_request(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    resources = Resources(store)
    model_server.respond = lambda request: (400, {'error': {'message': 'Rejected', 'type': 'invalid_request_error'}})
    with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
        invoke = lambda: client.chat.completions.create(model=model.model_id,
                    messages=[{'role': 'user', 'content': 'Inspect the parser.'}])
        with pytest.raises(APIStatusError):
            resources.execute(runtime, attempts[0].id, attempts[0].fence, key='bad-request', request=invoke)
        with pytest.raises(StoreError, match='request_replay'):
            resources.execute(runtime, attempts[0].id, attempts[0].fence, key='bad-request', request=invoke)
        assert len(model_server.requests) == 2
        assert resources.calls(owner, workflow.id)[0].status == 'failed'
        model_server.respond = lambda request: model_server.completion('[]')
        resources.execute(runtime, attempts[1].id, attempts[1].fence, key='next-request', request=invoke)
    assert store.budget(owner, workflow.id).spent.model_calls == 2


def test_unknown_http_outcome_holds_endpoint_across_restart_and_blocks_stop_ack(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    release = Event()
    model_server.respond = lambda request: (release.wait(timeout=3), model_server.completion('[]'))[1]
    try:
        with (OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0, timeout=.05) as client,
              pytest.raises(APITimeoutError)):
            Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='timeout',
                request=lambda: client.chat.completions.create(model=model.model_id,
                    messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
        connection = Store(store.path, catalogue=catalogue)
        try:
            resources = Resources(connection)
            assert resources.calls(owner, workflow.id)[0].status == 'uncertain'
            assert resources.begin(runtime, attempts[1].id, attempts[1].fence, key='after-timeout') is None
            assert connection.budget(owner, workflow.id).uncertain
            with pytest.raises(StoreError, match='resource_busy'):
                connection.confirm_stopped(runtime, attempts[0].id, attempts[0].fence,
                                           outcome='failed', expenditure_confirmed=True)
            assert len(model_server.requests) == 2
        finally:
            connection.close()
    finally:
        release.set()


def test_primary_client_accounts_for_generation_and_consolidation_at_http_boundary(active, model_server):
    from flycode.domain import Observation
    from flycode.llm.openai_compatible import OpenAICompatibleLLM
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    keys = iter(('generation', 'consolidation', 'over-budget'))
    client = OpenAICompatibleLLM(model=model.model_id, base_url=model_server.url, api_key='local-test',
        structured_output=True, request_executor=lambda request: Resources(store).execute(runtime,
            attempts[0].id, attempts[0].fence, key=next(keys), request=request))
    context = CortexContext(task='Inspect the parser.', behavior_mode='explore', observation=Observation(task='Inspect'))
    assert client.generate_candidates(context)[0].tool == 'read_file'
    client.consolidate(context, ['Inspected the parser.'])
    with pytest.raises(StoreError, match='budget_exhausted'):
        client.generate_candidates(context)
    assert len(model_server.requests) == 3 and client.request_count == 2
    assert store.budget(owner, workflow.id).spent.model_calls == 2
    assert len(Resources(store).calls(owner, workflow.id)) == 2


def test_http_does_not_hold_sqlite_transaction_and_metadata_edit_keeps_response_current(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    def response(request):
        connection = Store(store.path, catalogue=catalogue)
        try:
            task = connection.task(owner, attempts[0].task_id)
            connection.command(owner, task.id, TaskCommand(operation='set_metadata', expected_revision=task.revision,
                idempotency_key='during-http', changes={'title': 'Renamed during inference'}))
        finally:
            connection.close()
        return model_server.completion('[]')
    model_server.respond = response
    with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0, timeout=2) as client:
        Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='unlocked-http',
            request=lambda: client.chat.completions.create(model=model.model_id,
                messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
    assert store.task(owner, attempts[0].task_id).title == 'Renamed during inference'
    assert not Resources(store).calls(owner, workflow.id)[0].late


def test_outbox_failure_rolls_back_endpoint_and_debit(active):
    import sqlite3

    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    resources = Resources(store)
    store.db.execute("CREATE TEMP TRIGGER fail_model_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'disk'); END")
    with pytest.raises(sqlite3.IntegrityError):
        resources.begin(runtime, attempts[0].id, attempts[0].fence, key='atomic')
    assert resources.calls(owner, workflow.id) == []
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    store.db.execute('DROP TRIGGER fail_model_outbox')
    assert resources.begin(runtime, attempts[0].id, attempts[0].fence, key='atomic').fresh


def test_model_calls_and_replays_reject_reduced_scope(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    admission = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='scoped-call')
    reduced = runtime.model_copy(update={'project_ids': frozenset({'other'})})
    with pytest.raises(StoreError, match='scope'):
        Resources(store).begin(reduced, attempts[0].id, attempts[0].fence, key='scoped-call')
    with pytest.raises(StoreError, match='scope'):
        Resources(store).finish(reduced, admission.call.id, status='completed')
    with pytest.raises(StoreError, match='scope'):
        Resources(store).calls(reduced, workflow.id)
    assert store.budget(owner, workflow.id).spent.model_calls == 1


def test_invalid_provider_usage_does_not_leave_finished_http_holding_endpoint(active, model_server):
    from pydantic import ValidationError

    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, model = active
    def response(request):
        data = model_server.completion('[]')
        data['usage']['total_tokens'] = -1
        return data
    model_server.respond = response
    with (OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client,
          pytest.raises(ValidationError)):
        Resources(store).execute(runtime, attempts[0].id, attempts[0].fence, key='bad-usage',
            request=lambda: client.chat.completions.create(model=model.model_id,
                messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
    assert Resources(store).calls(owner, workflow.id)[0].status == 'failed'
    assert Resources(store).begin(runtime, attempts[1].id, attempts[1].fence, key='after-bad-usage').fresh


def test_restart_marks_open_transport_uncertain_without_replaying_or_freeing_endpoint(active):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, _, workflow, attempts, _ = active
    admission = Resources(store).begin(runtime, attempts[0].id, attempts[0].fence, key='crashed-request')
    store.recover(runtime)
    call = Resources(store).get(owner, admission.call.id)
    assert call.status == 'uncertain' and call.late
    assert call.ended_at is None and store.budget(owner, workflow.id).uncertain
    assert any(event.kind == 'model_uncertain' for event in store.events(owner, workflow.id))


async def test_real_response_after_fence_revocation_is_audited_and_never_returned(active, model_server):
    from flycode.orchestration.resources import Resources

    store, owner, runtime, catalogue, workflow, attempts, model = active
    entered, release = Event(), Event()
    def response(request):
        entered.set()
        assert release.wait(timeout=5)
        return model_server.completion('[]')
    model_server.respond = response
    def invoke():
        connection = Store(store.path, catalogue=catalogue)
        try:
            with OpenAI(base_url=model_server.url, api_key='local-test', max_retries=0) as client:
                return Resources(connection).execute(runtime, attempts[0].id, attempts[0].fence, key='late-response',
                    request=lambda: client.chat.completions.create(model=model.model_id,
                        messages=[{'role': 'user', 'content': 'Inspect the parser.'}]))
        finally:
            connection.close()
    worker = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        store.recover(runtime)
        release.set()
        with pytest.raises(StoreError, match='fence'):
            await asyncio.wait_for(worker, 3)
        call = Resources(store).calls(owner, workflow.id)[0]
        assert call.status == 'completed' and call.late and call.usage.total_tokens == 11
        assert store.budget(owner, workflow.id).spent.model_calls == 1
        store.confirm_stopped(runtime, attempts[0].id, attempts[0].fence,
                             outcome='retry_wait', expenditure_confirmed=True)
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
