"""Canonical tool effects require a current fence, permission and durable debit."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain import TaskSpec, ToolAction
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
def owned(tmp_path, model_server, request):
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store = Store(tmp_path / 'tools.sqlite', catalogue=catalogue)
    owner = AccessScope(actor_id='owner', project_ids={'project'}, role='owner')
    host = AccessScope(actor_id='host', project_ids={'project'}, role='runtime')
    permissions = ExecutionPermission(approved=True, read_scopes=getattr(request, 'param', ('.',)), write_scopes=('src',),
        tools=('read_file', 'list_files', 'search', 'edit_file', 'create_file', 'run_tests'))
    contract = AcceptanceContract(id='repair', version=1, criteria=[AcceptanceCriterion(
        id='result', check_ids=['regression'], expected_case_ids=['tests/test_example.py::test_example'])])
    draft = TaskDraft(title='Repair example', objective='Repair example without changing tests.',
        acceptance=contract, permissions=permissions, budget=BudgetAmount(model_calls=2, tool_calls=3))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='project', task=draft,
        budget=WorkflowBudget()), key='create')
    task = store.task(owner, workflow.root_task_id)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue'))
    store.start_next(owner, key='start')
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Repair only source.', project_ids=('project',), permissions=permissions, context_limit=8192))
    instance = store.register_instance(host, profile.id)
    attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=draft.budget, idempotency_key='claim')).attempt
    attempt = store.confirm_started(host, attempt.id, attempt.fence)
    repo = tmp_path / 'copy'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src/example.py').write_text('answer = 0\n')
    (repo / 'tests/test_example.py').write_text('def test_example():\n    assert True\n')
    yield store, owner, host, workflow, task, attempt, repo
    store.close()


async def test_actual_edit_spends_before_effect_and_replay_cannot_edit_twice(owned):
    from flycode.orchestration.environment import AttemptEnvironment
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, attempt, repo = owned
    env = AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)
    await env.reset(TaskSpec(id=task.id, task=task.objective))
    action = ToolAction(id='edit-1', tool='edit_file', arguments={'path': 'src/example.py',
                        'old_text': '0', 'new_text': '1'})
    transition = await env.step(action)
    assert transition.success and (repo / 'src/example.py').read_text() == 'answer = 1\n'
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    calls = Tools(store).calls(owner, workflow.id)
    assert len(calls) == 1 and calls[0].status == 'completed'
    assert calls[0].attempt_id == attempt.id and calls[0].instance_id == attempt.instance_id
    assert [e.kind for e in store.events(owner, workflow.id) if e.kind.startswith('tool_')] == ['tool_start', 'tool_end']
    with pytest.raises(StoreError, match='request_replay'):
        await env.step(action)
    assert (repo / 'src/example.py').read_text() == 'answer = 1\n'
    assert store.budget(owner, workflow.id).spent.tool_calls == 1


@pytest.mark.parametrize('tool,arguments', [
    ('edit_file', {'path': 'tests/test_example.py', 'content': 'changed'}),
    ('read_file', {'path': '../outside.txt'}),
    ('read_file', {'path': '.env'}),
    ('read_file', {'path': '.ENV'}),
    ('read_file', {'path': '/etc/passwd'}),
    ('create_file', {'path': 'src/../outside.txt', 'content': 'changed'}),
    ('run_tests', {'target': '--override-ini=addopts=unsafe'}),
    ('run_tests', {'target': '../outside.py'}),
    ('run_tests', {'service': 'unowned-container'}),
    ('docker_compose', {'action': 'down'}),
    ('read_file', {'path': 'src/example.py', 'repo_root': '/tmp'}),
])
async def test_denied_arguments_leave_bytes_budget_and_observation_untouched(owned, tool, arguments):
    from flycode.orchestration.environment import AttemptEnvironment

    store, owner, host, workflow, task, attempt, repo = owned
    env = AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)
    before = await env.reset(TaskSpec(id=task.id, task=task.objective))
    with pytest.raises(StoreError, match='permission|unsupported_tool'):
        await env.step(ToolAction(id='denied', tool=tool, arguments=arguments))
    assert await env.observe() == before
    assert store.budget(owner, workflow.id).spent.tool_calls == 0
    assert (repo / 'tests/test_example.py').read_text() == 'def test_example():\n    assert True\n'


async def test_local_tests_cannot_bypass_budget_and_local_stop_needs_no_inference(owned):
    from flycode.orchestration.environment import AttemptEnvironment

    store, owner, host, workflow, task, attempt, repo = owned
    env = AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)
    await env.reset(TaskSpec(id=task.id, task=task.objective))
    for index in range(3):
        transition = await env.step(ToolAction(id=f'local-tests-{index}', tool='run_tests', source='heuristic'))
        assert transition.success and transition.current.tests_status == 'passed'
    await env.reset(TaskSpec(id=task.id, task=task.objective))
    with pytest.raises(StoreError, match='budget_exhausted'):
        await env.step(ToolAction(id='local-tests-4', tool='run_tests', source='heuristic'))
    assert store.budget(owner, workflow.id).spent.tool_calls == 3
    assert store.budget(owner, workflow.id).spent.model_calls == 0
    assert (await env.step(ToolAction(id='local-stop', tool='stop', source='heuristic'))).success
    assert env.is_done()


async def test_pytest_timeout_retains_unknown_tool_ownership(owned):
    from flycode.orchestration.environment import AttemptEnvironment
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, attempt, repo = owned
    (repo / 'tests/test_example.py').write_text('import time\ndef test_example():\n    time.sleep(5)\n')
    env = AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)
    await env.reset(TaskSpec(id=task.id, task=task.objective))
    with pytest.raises(StoreError, match='reconcile_required'):
        await env.step(ToolAction(id='timeout', tool='run_tests', arguments={'timeout': .1}))
    call = Tools(store).calls(owner, workflow.id)[0]
    assert call.status == 'uncertain' and store.budget(owner, workflow.id).uncertain
    with pytest.raises(StoreError, match='resource_busy'):
        store.confirm_stopped(host, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_linked_and_special_paths_are_rejected_before_admission(owned, kind):
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, attempt, repo = owned
    outside = repo.parent / 'outside.txt'
    outside.write_text('keep these bytes')
    target = repo / 'src/unsafe'
    if kind == 'symlink':
        target.symlink_to(outside)
    elif kind == 'hardlink':
        os.link(outside, target)
    else:
        os.mkfifo(target)
    with pytest.raises(StoreError, match='permission'):
        Tools(store).begin(host, attempt.id, attempt.fence, root=repo,
            action=ToolAction(id='unsafe', tool='create_file', arguments={'path': 'src/unsafe', 'content': 'changed'}))
    assert store.budget(owner, workflow.id).spent.tool_calls == 0
    assert outside.read_text() == 'keep these bytes'


def test_two_connections_cannot_start_two_tools_in_one_attempt(owned):
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, attempt, repo = owned
    barrier = Barrier(2)
    def admit(index):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=3)
            return Tools(connection).begin(host, attempt.id, attempt.fence, root=repo,
                action=ToolAction(id=str(index), tool='read_file', arguments={'path': 'src/example.py'}))[0]
        except StoreError as error:
            return error.code
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, range(2)))
    assert results.count('resource_busy') == 1
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    assert len(Tools(store).calls(owner, workflow.id)) == 1


def test_recovery_holds_unknown_effect_and_late_ack_cannot_resume_worker(owned):
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, attempt, repo = owned
    call, _ = Tools(store).begin(host, attempt.id, attempt.fence, root=repo,
        action=ToolAction(id='inflight', tool='read_file', arguments={'path': 'src/example.py'}))
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        connection.recover(host)
        assert Tools(connection).get(owner, call.id).status == 'uncertain'
        with pytest.raises(StoreError, match='resource_busy'):
            connection.confirm_stopped(host, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
        late = Tools(store).finish(host, call.id, status='completed')
        assert late.late
        with pytest.raises(StoreError, match='fence'):
            Tools(store).begin(host, attempt.id, attempt.fence, root=repo,
                action=ToolAction(id='late-next', tool='read_file', arguments={'path': 'src/example.py'}))
        connection.confirm_stopped(host, attempt.id, attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
        assert connection.budget(owner, workflow.id).spent.tool_calls == 1
    finally:
        connection.close()


@pytest.mark.parametrize('owned', [('src',)], indirect=True)
def test_partial_read_profiles_are_blocked_until_observation_filtering_exists(owned):
    from flycode.orchestration.environment import AttemptEnvironment

    store, _, host, _, _, attempt, repo = owned
    with pytest.raises(StoreError, match='permission'):
        AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)


@pytest.mark.parametrize('operation', ['pause', 'cancel'])
async def test_owner_stop_prevents_any_next_tool_effect(owned, operation):
    from flycode.orchestration.environment import AttemptEnvironment

    store, owner, host, workflow, task, attempt, repo = owned
    env = AttemptEnvironment(repo, store, host, attempt.id, attempt.fence)
    await env.reset(TaskSpec(id=task.id, task=task.objective))
    current = store.task(owner, task.id)
    store.command(owner, task.id, TaskCommand(operation=operation, expected_revision=current.revision,
        idempotency_key=operation))
    with pytest.raises(StoreError, match='fence|paused'):
        await env.step(ToolAction(id='after-stop', tool='create_file', arguments={'path': 'src/after.py', 'content': 'bad'}))
    assert not (repo / 'src/after.py').exists()
    assert store.budget(owner, workflow.id).spent.tool_calls == 0


@pytest.mark.parametrize('version', [1, 2])
def test_old_store_upgrade_preserves_assignment_and_budget(owned, version):
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, attempt, repo = owned
    before = store.budget(owner, workflow.id)
    store.db.execute('DROP TABLE tool_calls')
    if version == 1:
        store.db.execute('DROP TABLE model_calls')
    store.db.execute(f'PRAGMA user_version={version}')
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        assert connection.budget(owner, workflow.id) == before
        call, _ = Tools(connection).begin(host, attempt.id, attempt.fence, root=repo,
            action=ToolAction(id='upgraded', tool='read_file', arguments={'path': 'src/example.py'}))
        assert call.attempt_id == attempt.id
        assert connection.budget(owner, workflow.id).spent.tool_calls == 1
    finally:
        connection.close()
