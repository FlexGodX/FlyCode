"""Canonical completion requires an immutable, reviewed and verified repair."""

import json

import pytest

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    ExecutionPermission,
    TaskCommand,
    TaskDraft,
)
from flycode.domain.verification import CheckSpec


def repair(owned, model_server, tmp_path):
    from flycode.orchestration.runtime import Runtime

    store, owner, host, _, _, attempt, source = owned
    (source / 'tests/test_example.py').write_text('import runpy\n'
        'def test_example():\n    assert runpy.run_path("src/example.py")["answer"] == 1\n')
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff user bytes\r\n')
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'edit_file', 'arguments': {'path': 'src/example.py', 'old_text': '0', 'new_text': '1'}}]}))
    runtime = Runtime(store, tmp_path / 'runtime')
    handle = runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(15), handle.error
        assert handle.process_stopped.is_set() and runtime.worker(owner, attempt.id).stop_confirmed
        assert handle.error is None
    finally:
        handle.close()
    return runtime


def checks():
    return [CheckSpec(id=stage, stage=stage, expected_outcome='failed' if stage == 'baseline' else 'passed',
        targets=['tests/test_example.py::test_example'], dependencies=[] if stage == 'baseline' else [dependency])
        for stage, dependency in [('baseline', None), ('target', 'baseline'),
                                  ('regression', 'target'), ('integration', 'regression')]]


def reviewer(owned, artifacts, *, tools=24):
    store, owner, host, workflow, author, attempt, _ = owned
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    profile = store.register_profile(owner, AgentProfileDraft(role='reviewer',
        model_ref=attempt.profile_snapshot.model_ref, instructions='Verify the exact approved artifact.',
        project_ids=(author.project_id,), permissions=permission, context_limit=8192))
    task = store.create_task(owner, workflow.id, TaskDraft(title='Verify repair', kind='review',
        objective='Independently verify the exact repair with the approved oracle.', parent_id=author.id,
        acceptance=author.acceptance, permissions=permission, budget=BudgetAmount(tool_calls=tools),
        input_artifacts={artifact.id: artifact.patch_hash for artifact in artifacts}), key='review-task-' + str(len(store.tasks(owner, workflow.id))))
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-' + task.id))
    instance = store.register_instance(host, profile.id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key='claim-' + task.id))
    assert claim.attempt is not None, claim.reasons
    return store.confirm_started(host, claim.attempt.id, claim.attempt.fence)


@pytest.fixture
def delivery(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    store, owner, host, _, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    return runtime, artifact, review, approval


@pytest.mark.parametrize('operation', ['pause', 'cancel', 'release_assignment'])
def test_published_assignment_can_stop_without_touching_a_reused_instance(owned, model_server, tmp_path, operation):
    from flycode.orchestration.artifacts import Artifacts

    store, owner, host, workflow, root, root_attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)

    def child(key, instance=None):
        task = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
            parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key=key)
        task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key=key+'-enqueue'))
        instance = instance or store.register_instance(host, root_attempt.profile_id)
        result = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key=key+'-claim'))
        assert result.attempt is not None, result.reasons
        return task, store.confirm_started(host, result.attempt.id, result.attempt.fence)

    task, author = child('author')
    repair((store, owner, host, workflow, task, author, source), model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    _, replacement = child('replacement', store.instance(host, author.instance_id))
    instance = store.instance(host, author.instance_id)
    before = store.budget(owner, workflow.id).spent
    current = store.task(owner, task.id)
    command = TaskCommand(operation=operation, expected_revision=current.revision, idempotency_key='stop-published')
    result = store.command(owner, current.id, command)
    assert result.current_attempt_id is None
    assert result.state == ('cancelled' if operation == 'cancel' else 'retry_wait')
    assert result.paused is (operation == 'pause')
    assert store._attempt(author.id).state == ('cancelled' if operation == 'cancel' else 'interrupted')
    assert store._attempt(author.id).stop_confirmed
    assert store.instance(host, author.instance_id) == instance
    assert store.instance(host, author.instance_id).current_attempt_id == replacement.id
    assert store.budget(owner, workflow.id).spent == before
    assert Artifacts(store, runtime.root).get(owner, artifact.id) == artifact
    assert store.command(owner, current.id, command) == result


def test_late_write_to_displaced_original_blocks_canonical_success(owned, delivery, monkeypatch):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    integration = Integration(store, runtime.root)
    verify = integration._verify
    with (source / 'src/example.py').open('r+b') as original:
        def verify_then_edit(*args, **kwargs):
            record = verify(*args, **kwargs)
            if kwargs['applied']:
                original.write(b'user late bytes\n')
                original.truncate()
                original.flush()
            return record
        monkeypatch.setattr(integration, '_verify', verify_then_edit)
        with pytest.raises(StoreError, match='integration_conflict'):
            integration.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
    assert store.workflow(owner, workflow.id).state != 'succeeded'
    assert store.task(owner, task.id).state != 'succeeded'
    assert (source / 'src/example.py').read_bytes() == b'user late bytes\n'


def test_integrator_cannot_share_an_active_worker(owned, delivery):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    # A reviewer worker performs real registered tests even without model budget.
    handle = runtime.launch(host, integrator.id, integrator.fence, source, brain='heuristic_fsm')
    try:
        with pytest.raises(StoreError, match='worker_exists'):
            Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        assert not any(call.tool == 'integrate' for call in Tools(store).calls(owner, workflow.id))
        assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    finally:
        handle.close()


def test_runtime_cannot_start_inside_an_admitted_host_review(owned, delivery, monkeypatch):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, _, host, _, _, _, source = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    reviews = Reviews(store, runtime.root)
    from flycode.verification import VerificationEngine
    verify = VerificationEngine.verify_plan
    blocked = []

    def verify_with_competing_worker(engine, *args, **kwargs):
        try:
            handle = runtime.launch(host, checker.id, checker.fence, source, brain='heuristic_fsm')
        except StoreError as error:
            assert error.code == 'execution_busy'
            blocked.append(True)
        else:
            handle.close()
            pytest.fail('A worker started while host verification owned the attempt')
        return verify(engine, *args, **kwargs)

    monkeypatch.setattr(VerificationEngine, 'verify_plan', verify_with_competing_worker)
    result = reviews.run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'approved' and blocked == [True]


def test_live_root_assignment_blocks_integration_of_child_patch(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, root, root_attempt, source = owned
    child_permissions = root.permissions
    store.confirm_stopped(host, root_attempt.id, root_attempt.fence, outcome='retry_wait', expenditure_confirmed=True)
    root = store.task(owner, root.id)
    root = store.command(owner, root.id, TaskCommand(operation='revise', expected_revision=root.revision,
        idempotency_key='read-only-root', changes={'permissions': ExecutionPermission(approved=True,
            read_scopes=('.',), tools=('read_file', 'run_tests')).model_dump()}))
    root = store.command(owner, root.id, TaskCommand(operation='retry', expected_revision=root.revision, idempotency_key='retry-root'))
    root_instance = store.instance(host, root_attempt.instance_id)
    root_claim = store.claim(host, ClaimRequest(instance_id=root_instance.id, expected_instance_revision=root_instance.revision,
        task_id=root.id, expected_task_revision=root.revision, reserve=root.budget, idempotency_key='reclaim-root'))
    assert root_claim.attempt is not None, root_claim.reasons
    root_attempt = store.confirm_started(host, root_claim.attempt.id, root_claim.attempt.fence)
    root = store.task(owner, root.id)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=child_permissions, budget=root.budget), key='child-author')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.register_instance(host, root_attempt.profile_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    runtime = repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='incomplete_workflow'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert store.task(owner, root.id).current_attempt_id == root_attempt.id
    assert store._attempt(root_attempt.id).state == 'running'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_unselected_published_root_cannot_be_completed_by_child_patch(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, root, root_attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key='child-author')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.register_instance(host, root_attempt.profile_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='incomplete_workflow'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert store.task(owner, root.id).state == 'awaiting_review'
    assert store.task(owner, root.id).current_attempt_id == root_attempt.id
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('tamper', ['manifest', 'blob', 'mode', 'worker-result'])
def test_tampered_patch_cannot_enter_review_or_spend(owned, delivery, tamper):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    directory = runtime.root / 'artifacts' / artifact.id
    if tamper == 'mode':
        (directory / 'target/src/example.py').chmod(0o755)
    else:
        name = {'manifest': 'manifest.json', 'blob': 'target/src/example.py', 'worker-result': 'run-result.json'}[tamper]
        with (directory / name).open('ab') as stream:
            stream.write(b' altered bytes')
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='artifact_integrity'):
        Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert store.budget(owner, workflow.id).spent == before
    assert not any(call.attempt_id == checker.id for call in Tools(store).calls(owner, workflow.id))


def test_review_replay_rejects_changed_rerun_policy_without_spending(owned, delivery):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, review, approval = delivery
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='idempotency'):
        Reviews(store, runtime.root).run(host, review.reviewer_attempt_id, review.reviewer_fence,
            artifact.id, approval_hash=approval, reruns=2)
    assert store.budget(owner, workflow.id).spent == before


def test_review_budget_cannot_approve_unexecuted_registered_checks(owned, delivery):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact], tools=2)
    before = store.budget(owner, workflow.id).spent
    result = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'inconclusive'
    assert result.verdict.status != 'passed'
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 2
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 2
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert store.task(owner, checker.task_id).state == 'failed'


def test_owner_cancel_after_checks_retains_evidence_and_settles_review(owned, delivery, monkeypatch):
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    verify = VerificationEngine.verify_plan
    before = store.budget(owner, workflow.id).spent

    def cancel_after_checks(engine, *args, **kwargs):
        verdict = verify(engine, *args, **kwargs)
        assert verdict.status == 'passed'
        task = store.task(owner, checker.task_id)
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=task.revision,
            idempotency_key='cancel-review-at-boundary'))
        return verdict

    monkeypatch.setattr(VerificationEngine, 'verify_plan', cancel_after_checks)
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'inconclusive' and review.verdict.status == 'passed'
    assert store.task(owner, checker.task_id).state == 'cancelled'
    assert store.task(owner, checker.task_id).current_attempt_id is None
    assert store._attempt(checker.id).state == 'cancelled' and store._attempt(checker.id).stop_confirmed
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 4
    assert (runtime.root / 'verification' / review.verdict.evidence_id / 'manifest.json').exists()


def test_changed_source_is_preserved_and_blocks_integration_before_checks(owned, delivery):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, source = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    (source / 'src/example.py').write_bytes(b'user changed source\n')
    before = store.budget(owner, workflow.id).spent
    with pytest.raises(StoreError, match='integration_conflict'):
        Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    assert store.budget(owner, workflow.id).spent == before
    assert (source / 'src/example.py').read_bytes() == b'user changed source\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert store.workflow(owner, workflow.id).state != 'succeeded'


def test_actual_stopped_worker_publishes_exact_patch_without_touching_user_source(owned, model_server, tmp_path):
    from flycode.orchestration.artifacts import Artifacts

    store, owner, _, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    assert artifact.attempt_id == attempt.id and artifact.contract_hash == attempt.contract_hash
    assert [change.path for change in artifact.changes] == ['src/example.py']
    assert artifact.base.files['src/example.py'] != artifact.target.files['src/example.py']
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert store._attempt(attempt.id).stop_confirmed
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert any(event.kind == 'artifact_published' for event in store.events(owner, workflow.id))


def test_independent_review_executes_and_charges_each_real_registered_check(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    checker = reviewer(owned, [artifact])
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    before = store.budget(owner, workflow.id).spent
    result = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert result.status == 'approved' and result.patch_hash == artifact.patch_hash
    assert result.verdict.status == 'passed' and result.verdict.required_coverage == 1
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 4 and all(call.tool == 'run_tests' and call.status == 'completed' for call in calls)
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 4
    assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls
    assert store.task(owner, checker.task_id).state == 'succeeded'
    assert store.task(owner, task.id).state == 'awaiting_review'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_real_repair_review_integration_application_and_current_verified_completion(owned, model_server, tmp_path):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, attempt, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(owned, [artifact])
    result = Integration(store, runtime.root).run(host, integrator.id, integrator.fence,
        (artifact.id,), (review.id,), approval_hashes={task.id: approval, integrator.task_id: approval})
    assert result.state == 'completed'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert engine.current(result.applied_verdicts[task.id], source, contract=task.acceptance)
    assert store.task(owner, task.id).state == 'succeeded'
    assert store.task(owner, integrator.task_id).state == 'succeeded'
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store._attempt(attempt.id).stop_confirmed
    calls = Tools(store).calls(owner, workflow.id)
    assert len([call for call in calls if call.attempt_id == integrator.id]) == 17
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
