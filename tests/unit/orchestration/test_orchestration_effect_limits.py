"""Default ceilings are real Host HTTP/filesystem/pytest effects, not ledger arithmetic."""
# ruff: noqa: F401,F811

import json
import sqlite3

import pytest
from openai import OpenAI
from test_managed_coordinator import managed_pipeline
from test_store import command, draft

from flycode.domain import TaskSpec, ToolAction
from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    ExecutionPermission,
)
from flycode.domain.verification import CheckSpec
from flycode.domain.verification_control import (
    VerificationPlanDraft,
    VerificationProfileDraft,
)
from flycode.orchestration.environment import AttemptEnvironment
from flycode.orchestration.host import Host
from flycode.orchestration.resources import Resources
from flycode.orchestration.store import StoreError
from flycode.orchestration.tools import Tools
from flycode.orchestration.verification import CheckExecutor
from flycode.orchestration.workspaces import Workspaces
from flycode.verification.custody import stopped
from flycode.verification.engine import VerificationEngine
from flycode.verification.registry import VerificationRegistry


async def test_actual_sixty_http_and_one_hundred_twenty_tools_protect_review_reserve(managed_pipeline, model_server):
    """An extra debit or lost protected reserve must cause a real observable excess."""
    observer, owner, runtime, workflow, tasks, source, host_root, root, *_ = managed_pipeline
    catalogue = observer.catalogue
    prior_requests = len(model_server.requests)
    unlocked_requests = []
    respond = model_server.respond

    def observe_transport(request):
        connection = sqlite3.connect(observer.path, timeout=1)
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
            unlocked_requests.append(request)
        finally:
            connection.close()
        return respond(request)

    model_server.respond = observe_transport
    effect = source.parent / 'registered-pressure-effects'
    (source / 'tests/test_pressure_effect.py').write_text('from pathlib import Path\n'
        'def test_effect():\n'
        f'    with Path({str(effect)!r}).open("ab") as output:\n'
        '        output.write(b"checked\\n")\n')
    source_bytes = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    project_id = workflow.project_id
    model_ref = observer._profile(observer.instances(runtime)[0].profile_id).model_ref
    read = ExecutionPermission(approved=True, read_scopes=('.',), tools=('read_file',))
    check = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))

    with Host(observer.path, host_root) as leader:
        store = leader.connect(catalogue=catalogue)
        try:
            developer = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model_ref,
                instructions='Inspect only approved source.', capabilities=('python',), project_ids=(project_id,),
                permissions=read, context_limit=8192))
            reviewer = store.register_profile(owner, AgentProfileDraft(role='reviewer', model_ref=model_ref,
                instructions='Run only the registered checks.', capabilities=('python',), project_ids=(project_id,),
                permissions=check, context_limit=8192))
            work = store.create_task(owner, workflow.id, draft(read, parent_id=tasks[0].id,
                budget=BudgetAmount(model_calls=60, tool_calls=120), allowed_profile_ids=(developer.id,)), key='effect-work')
            probe = store.create_task(owner, workflow.id, draft(read, parent_id=tasks[0].id,
                budget=BudgetAmount(model_calls=1, tool_calls=1), allowed_profile_ids=(developer.id,)), key='effect-probe')
            review = store.create_task(owner, workflow.id, draft(check, kind='review', parent_id=tasks[0].id,
                budget=BudgetAmount(model_calls=12, tool_calls=24), allowed_profile_ids=(reviewer.id,)), key='effect-review')
            review_probe = store.create_task(owner, workflow.id, draft(check, kind='review', parent_id=tasks[0].id,
                budget=BudgetAmount(model_calls=1, tool_calls=1), allowed_profile_ids=(reviewer.id,)), key='effect-review-probe')
            for task in (tasks[0], work, probe, review, review_probe):
                command(store, owner, task, 'enqueue')
            store.start_next(owner, key='effect-start')
            instances = [store.register_instance(runtime, profile.id) for profile in (developer, developer, reviewer, reviewer)]

            def claim(task, instance, reserve, key):
                current = store.task(owner, task.id)
                current_instance = store.instance(runtime, instance.id)
                return store.claim(runtime, ClaimRequest(instance_id=instance.id,
                    expected_instance_revision=current_instance.revision, task_id=task.id,
                    expected_task_revision=current.revision, reserve=reserve, idempotency_key=key))

            registry = VerificationRegistry(store)
            subject = registry.register_subject(owner, project_id)
            checks = [CheckSpec(id=f'pressure-{number}', targets=['tests/test_pressure_effect.py::test_effect'], timeout=10)
                for number in range(8)]
            plans = []
            for number, check_spec in enumerate(checks):
                profile = registry.register_profile(owner, project_id,
                    VerificationProfileDraft(title='Pressure suite ' + str(number), checks=[check_spec], reruns=2))
                plans.append(registry.create_plan(owner, VerificationPlanDraft(subject_ids={'target': subject.id},
                    profile_id=profile.id, profile_version=profile.version,
                    budget={'max_tool_calls': 3, 'max_attempts': 3, 'wall_seconds': 900},
                    idempotency_key=f'pressure-plan-{number}')))
            registered_root = registry.subject_root(owner, subject.id)
            for amount in (BudgetAmount(model_calls=49, tool_calls=96), BudgetAmount(model_calls=48, tool_calls=97)):
                denied = claim(work, instances[0], amount, 'oversized-' + amount.model_dump_json())
                assert denied.attempt is None and 'verification_reserve' in denied.reasons
            assert not store.attempts(owner, workflow.id)
            amount = BudgetAmount(model_calls=48, tool_calls=96)
            author = claim(work, instances[0], amount, 'effect-author').attempt
            assert author is not None
            store.confirm_started(runtime, author.id, author.fence)
            deadline = store.workflow(owner, workflow.id).deadline_at
            assert deadline is not None
            workspaces = Workspaces(store, root / 'workspaces')
            workspaces.prepare(runtime, author.id, author.fence, source)
            repository = workspaces.repository(owner, author.id)
            environment = AttemptEnvironment(repository, store, runtime, author.id, author.fence)
            await environment.reset(TaskSpec(id=work.id, task='Inspect the approved parser.',
                max_steps=120, max_tool_calls=120))
            resources = Resources(store)
            with OpenAI(base_url=catalogue.base_url, api_key='local-test', max_retries=0, timeout=5) as client:
                def invoke(attempt, key):
                    assert not store.db.in_transaction
                    return resources.execute(runtime, attempt.id, attempt.fence, key=key,
                        request=lambda: client.chat.completions.create(model=attempt.profile_snapshot.loaded_instance_id,
                            messages=[{'role': 'user', 'content': 'Inspect the approved parser. Return one safe observation.'}]))

                for number in range(48):
                    response = invoke(author, f'developer-http-{number}')
                    assert response.usage.total_tokens == 11
                    if number % 8 == 0:
                        store.heartbeat(runtime, author.id, author.fence)
                for number in range(96):
                    result = await environment.step(ToolAction(id=f'developer-read-{number}', tool='read_file',
                        arguments={'path': 'src/parser.py'}))
                    assert result.success and 'value = 0' in result.stdout
                assert store.budget(owner, workflow.id).spent == amount
                assert store.budget(owner, workflow.id).free == BudgetAmount(model_calls=12, tool_calls=24)

                def forbidden_effect():
                    pytest.fail('A request beyond the owned budget reached external execution')

                with pytest.raises(StoreError, match='budget_exhausted'):
                    resources.execute(runtime, author.id, author.fence, key='developer-http-49', request=forbidden_effect)
                before = await environment.observe()
                with pytest.raises(StoreError, match='budget_exhausted'):
                    await environment.step(ToolAction(id='developer-read-97', tool='read_file', arguments={'path': 'src/parser.py'}))
                assert await environment.observe() == before
                for number, reserve in enumerate((BudgetAmount(model_calls=1), BudgetAmount(tool_calls=1))):
                    denied = claim(probe, instances[1], reserve, f'protected-probe-{number}')
                    assert denied.attempt is None and 'verification_reserve' in denied.reasons
                store.confirm_stopped(runtime, author.id, author.fence, outcome='failed', expenditure_confirmed=True)
                checker = claim(review, instances[2], BudgetAmount(model_calls=12, tool_calls=24), 'effect-checker').attempt
                assert checker is not None and checker.verification
                store.confirm_started(runtime, checker.id, checker.fence)
                for number in range(12):
                    response = invoke(checker, f'reviewer-http-{number}')
                    assert response.usage.total_tokens == 11
                    store.heartbeat(runtime, checker.id, checker.fence)

                executor = CheckExecutor(store, runtime, checker.id, checker.fence)
                engine = VerificationEngine(root / 'pressure-evidence', invocation_executor=executor.execute)
                invocations = []
                for record in plans:
                    verdict = engine.verify_plan(record.plan, {'target': registered_root}, should_stop=executor.should_stop)
                    assert verdict.status == 'passed' and verdict.scope == 'registered-suite', verdict.reasons
                    manifest = json.loads((engine.data_dir / verdict.evidence_id / 'manifest.json').read_text())
                    assert manifest['complete'] and len(manifest['invocations']) == 3
                    invocations.extend(manifest['invocations'])
                assert len(invocations) == 24
                assert all(item['complete'] and item['stop_confirmed'] for item in invocations)
                assert effect.read_bytes() == b'checked\n' * 24
                assert store.budget(owner, workflow.id).spent == BudgetAmount(model_calls=60, tool_calls=120)
                with pytest.raises(StoreError, match='budget_exhausted'):
                    resources.execute(runtime, checker.id, checker.fence, key='workflow-http-61', request=forbidden_effect)
                denied_check = executor.execute(checks[0], 1, lambda **kwargs: forbidden_effect())
                assert denied_check.cancelled and denied_check.reasons == ['budget_exhausted']
                assert effect.read_bytes() == b'checked\n' * 24
                for number, reserve in enumerate((BudgetAmount(model_calls=1), BudgetAmount(tool_calls=1))):
                    denied = claim(review_probe, instances[3], reserve, f'exhausted-global-probe-{number}')
                    assert denied.attempt is None and 'budget_exhausted' in denied.reasons

            calls = resources.calls(owner, workflow.id)
            tools = Tools(store).calls(owner, workflow.id)
            assert len(model_server.requests) - prior_requests == len(unlocked_requests) == 60
            assert len(calls) == 60 and all(call.status == 'completed' and call.usage.total_tokens == 11 for call in calls)
            assert len(tools) == 120 and all(call.status == 'completed' for call in tools)
            registered = [call for call in tools if call.execution_profile == 'registered-pytest-v1']
            assert len(registered) == 24
            for call in registered:
                with stopped(call.process) as receipt:
                    assert receipt.stop_confirmed and receipt.reason == 'completed'
            store.confirm_stopped(runtime, checker.id, checker.fence, outcome='failed', expenditure_confirmed=True)
            budget = store.budget(owner, workflow.id)
            assert budget.reserved == budget.free == BudgetAmount() and not budget.uncertain
            assert store.workflow(owner, workflow.id).deadline_at == deadline
            assert store.workflow(owner, workflow.id).state != 'succeeded'
            assert len(store.attempts(owner, workflow.id)) == 2
            assert all(attempt.stop_confirmed for attempt in store.attempts(owner, workflow.id))
            assert {str(path.relative_to(source)): path.read_bytes()
                for path in source.rglob('*') if path.is_file()} == source_bytes
        finally:
            store.close()
