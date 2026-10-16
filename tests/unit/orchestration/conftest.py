"""Canonical running-attempt fixture shared by runtime integration checks."""

import json

import pytest

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
from flycode.orchestration.store import Store


@pytest.fixture
def owned(tmp_path, model_server, request):
    parameter = getattr(request, 'param', ('.',))
    options = parameter if isinstance(parameter, dict) else {'read_scopes': parameter}
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store = Store(tmp_path / 'tools.sqlite', catalogue=catalogue)
    owner = AccessScope(actor_id='owner', project_ids={'project'}, role='owner')
    host = AccessScope(actor_id='host', project_ids={'project'}, role='runtime')
    permissions = ExecutionPermission(approved=True, read_scopes=options.get('read_scopes', ('.',)), write_scopes=('src',),
        tools=options.get('tools', ('read_file', 'list_files', 'search', 'edit_file', 'create_file', 'run_tests')))
    contract = AcceptanceContract(id='repair', version=1, criteria=[AcceptanceCriterion(
        id='result', check_ids=['regression'], expected_case_ids=['tests/test_example.py::test_example'])])
    draft = TaskDraft(title='Repair example', objective='Repair example without changing tests.',
        acceptance=contract, permissions=permissions, budget=BudgetAmount(model_calls=2, tool_calls=3),
        base_snapshot_hash=options.get('base_snapshot_hash'))
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
    if not options.get('assigned'):
        attempt = store.confirm_started(host, attempt.id, attempt.fence)
    repo = tmp_path / 'copy'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src/example.py').write_text('answer = 0\n')
    (repo / 'tests/test_example.py').write_text('def test_example():\n    assert True\n')
    yield store, owner, host, workflow, task, attempt, repo
    store.close()
