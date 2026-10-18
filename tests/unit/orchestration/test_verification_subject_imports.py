"""Verification imports derive physical inputs and references from host records."""
import hashlib
import shutil

import pytest
from test_canonical_project_registry import queued

from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, StoreError, changed
from flycode.orchestration.workspaces import Workspaces, _git
from flycode.verification.registry import VerificationRegistry
from flycode.verification.snapshot import digest


@pytest.fixture
def workspaced(tmp_path, request):
    from flycode.domain.orchestration import WorkflowBudget

    parameter = getattr(request, 'param', False)
    options = parameter if isinstance(parameter, dict) else {'git': parameter}
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'answer.py').write_text('answer = 0\n')
    if options.get('git'):
        _git(source, 'init', '--quiet', '--template=')
        _git(source, 'add', '.')
        _git(source, 'commit', '--quiet', '-m', 'Owner baseline')
    (source / 'answer.py').write_text('answer = 1\n')
    (source / 'untracked.bin').write_bytes(b'\x00\xff original\r\n')
    store = Store(tmp_path / 'state.sqlite')
    project = Projects(store).register(source, actor_id='owner')
    owner, host, workflow, task, _ = queued(store, project.id,
        budget=WorkflowBudget(max_tool_calls=options.get('tools', 120), max_instances=options.get('instances', 4)))
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    binding = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind')
    result = dispatcher.select(host, key='select')
    assert result.attempt is not None, result.reasons
    attempt = result.attempt
    assert attempt.execution_binding_id == binding.id
    workspaces = Workspaces(store, dispatcher.root / 'workspaces')
    workspace = workspaces.prepare(host, attempt.id, attempt.fence, source)
    attempt = store.confirm_started(host, attempt.id, attempt.fence)
    try:
        yield store, owner, host, project, workflow, task, attempt, binding, workspace, dispatcher.root, source
    finally:
        store.close()


@pytest.mark.parametrize('workspaced', [False, True], indirect=True, ids=['non-git', 'git'])
def test_workspace_import_preserves_original_base_bytes_and_real_attempt_reference(workspaced):
    store, owner, _, project, workflow, task, attempt, binding, workspace, runtime, source = workspaced
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_workspace_subject'), 'Host-owned workspace subject import is missing'
    subject = registry.register_workspace_subject(owner, attempt.id)
    assert subject.project_id == project.id
    assert subject.snapshot.ref.model_dump() == {'project_id': project.id, 'workflow_id': workflow.id,
        'task_id': task.id, 'attempt_id': attempt.id}
    assert subject.snapshot.base == workspace.snapshot.base
    assert subject.snapshot.files == workspace.snapshot.files
    assert subject.snapshot.inventory == workspace.snapshot.inventory
    assert subject.snapshot.subject_hash == digest({'files': subject.snapshot.files, 'modes': subject.snapshot.modes,
        'base': workspace.snapshot.base, 'environment': subject.snapshot.environment, 'ref': subject.snapshot.ref.model_dump()})
    assert subject.origin.kind == 'workspace-baseline' and subject.origin.record_id == workspace.id
    assert subject.origin.record_hash == digest(workspace.model_dump(mode='json'))
    assert subject.origin.source_base == workspace.snapshot.base
    assert subject.origin.source_subject_hash == workspace.snapshot.subject_hash
    (source / 'untracked.bin').write_bytes(b'owner changed source')
    copy = registry.subject_root(owner, subject.id)
    assert (copy / 'untracked.bin').read_bytes() == b'\x00\xff original\r\n'
    assert not (copy / '.git').exists()
    assert binding.runtime_root == str(runtime)
    assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0


def test_import_rejects_manual_or_mismatched_assignment_without_searching_paths(workspaced):
    store, owner, _, _, _, _, attempt, binding, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_workspace_subject'), 'Host-owned workspace subject import is missing'
    original = attempt.model_dump_json(exclude={'reserved', 'spent'})
    store.db.execute('UPDATE attempts SET data=? WHERE id=?',
        (changed(attempt, execution_binding_id=None).model_dump_json(exclude={'reserved', 'spent'}), attempt.id))
    with pytest.raises(StoreError, match='verification_provenance_unavailable'):
        registry.register_workspace_subject(owner, attempt.id)
    store.db.execute('UPDATE attempts SET data=? WHERE id=?', (original, attempt.id))
    store.db.execute('UPDATE worker_bindings SET data=? WHERE id=?',
        (changed(binding, workflow_id='foreign-workflow').model_dump_json(), binding.id))
    with pytest.raises(StoreError, match='verification_subject_scope'):
        registry.register_workspace_subject(owner, attempt.id)
    assert not store.db.execute('SELECT 1 FROM verification_subjects').fetchone()


def test_changed_registered_workspace_baseline_never_becomes_a_new_approved_subject(workspaced):
    store, owner, _, _, _, _, attempt, _, workspace, runtime, _ = workspaced
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_workspace_subject'), 'Host-owned workspace subject import is missing'
    baseline = runtime / 'workspaces' / workspace.id / 'baseline' / 'answer.py'
    baseline.write_text('answer = 999\n')
    with pytest.raises(StoreError, match='workspace_integrity'):
        registry.register_workspace_subject(owner, attempt.id)
    assert not store.db.execute('SELECT 1 FROM verification_subjects').fetchone()
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() != workspace.snapshot.files['answer.py']


def test_owner_cancel_between_last_import_read_and_publication_leaves_no_subject(workspaced, monkeypatch):
    from flycode.domain.orchestration import TaskCommand

    store, owner, _, _, _, task, attempt, _, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    unchanged = registry._unchanged

    def cancel_before_publication(project, storage):
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision,
            idempotency_key='cancel-during-import'))
        return unchanged(project, storage)

    monkeypatch.setattr(registry, '_unchanged', cancel_before_publication)
    with pytest.raises(StoreError, match='verification_subject_scope|contract_changed'):
        registry.register_workspace_subject(owner, attempt.id)
    assert not store.db.execute('SELECT 1 FROM verification_subjects').fetchone()


def suite_plan(registry, owner, subject, *, key):
    from flycode.domain.verification_control import (
        VerificationPlanDraft,
        VerificationProfileDraft,
    )

    profile = registry.register_profile(owner, subject.project_id, VerificationProfileDraft(title='Original suite'))
    return registry.create_plan(owner, VerificationPlanDraft(subject_ids={'target': subject.id}, profile_id=profile.id,
        profile_version=profile.version, budget={'max_tool_calls': 6, 'max_attempts': 3, 'wall_seconds': 900.},
        idempotency_key=key))


def cli_subject(store, source, *arguments):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    import flycode

    result = subprocess.run([sys.executable, '-m', 'flycode.cli', 'verification', '--database', str(store.path),
        'register-subject', '--project', str(source), *arguments],
        env={**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).parent.parent)},
        capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['id']


def test_trusted_cli_imports_workspace_by_owned_attempt_id_without_running_a_worker(workspaced):
    store, owner, _, _, _, _, attempt, _, _, _, source = workspaced
    subject = VerificationRegistry(store).subject(owner, cli_subject(store, source, '--workspace-attempt', attempt.id))
    assert subject.snapshot.ref.attempt_id == attempt.id and subject.origin.kind == 'workspace-baseline'
    assert store.db.execute('SELECT count(*) FROM workers').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM model_calls').fetchone()[0] == 0
    assert (source / 'untracked.bin').read_bytes() == b'\x00\xff original\r\n'


@pytest.mark.parametrize('workspaced', [{'tools': 5}], indirect=True)
def test_workflow_check_uses_the_existing_budget_and_verification_reserve(workspaced):
    from flycode.domain.verification_control import VerificationCommand
    from flycode.verification.control import VerificationControl

    store, owner, _, _, workflow, _, attempt, _, _, _, _ = workspaced
    registry, ctl = VerificationRegistry(store), VerificationControl(store)
    subject = registry.register_workspace_subject(owner, attempt.id)
    first = suite_plan(registry, owner, subject, key='first-plan')
    second = suite_plan(registry, owner, subject, key='second-plan')
    before = store.budget(owner, workflow.id)
    assert before.reserved.tool_calls == 4 and before.free.tool_calls == 1
    started = ctl.command(owner, first.id, VerificationCommand(operation='start', expected_revision=1, idempotency_key='start-first'))
    assert first.workflow_id == workflow.id and first.task_id == attempt.task_id
    after = store.budget(owner, workflow.id)
    assert after.reserved.tool_calls == 5 and after.free.tool_calls == 0 and after.spent == before.spent
    assert started.deadline_at <= store.workflow(owner, workflow.id).deadline_at
    with pytest.raises(StoreError, match='budget'):
        ctl.command(owner, second.id, VerificationCommand(operation='start', expected_revision=1, idempotency_key='start-second'))
    ctl.command(owner, first.id, VerificationCommand(operation='cancel', expected_revision=2, idempotency_key='cancel-first'))
    assert store.budget(owner, workflow.id) == before
    assert ctl.command(owner, second.id, VerificationCommand(operation='start', expected_revision=1,
        idempotency_key='start-second')).number == 1
    assert len(store.attempts(owner, workflow.id)) == 1
    assert not store.db.execute('SELECT 1 FROM model_calls').fetchone()
    assert not store.db.execute('SELECT 1 FROM tool_calls').fetchone()


@pytest.mark.parametrize('workspaced', [{'tools': 5}], indirect=True)
def test_different_plans_racing_two_connections_cannot_overbook_workflow_reserve(workspaced):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.domain.verification_control import VerificationCommand
    from flycode.verification.control import VerificationControl

    store, owner, _, _, workflow, _, attempt, _, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    subject = registry.register_workspace_subject(owner, attempt.id)
    plans = [suite_plan(registry, owner, subject, key=f'plan-{i}') for i in range(2)]
    barrier = Barrier(2)

    def start(plan):
        connection = Store(store.path)
        try:
            barrier.wait(timeout=5)
            try:
                return VerificationControl(connection).command(owner, plan.id, VerificationCommand(
                    operation='start', expected_revision=1, idempotency_key='start-' + plan.id))
            except StoreError as error:
                return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, plans))
    assert len([result for result in results if not isinstance(result, str)]) == 1
    assert [result for result in results if isinstance(result, str)] == ['budget']
    assert store.budget(owner, workflow.id).reserved.tool_calls == 5
    assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1


def test_superseded_approval_blocks_new_work_and_preserves_exact_create_receipt(workspaced, tmp_path):
    from flycode.domain.verification import CheckSpec
    from flycode.domain.verification_control import (
        VerificationCommand,
        VerificationPlanDraft,
        VerificationProfileDraft,
    )
    from flycode.verification.control import VerificationControl

    store, owner, _, _, _, task, attempt, _, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    subject = registry.register_workspace_subject(owner, attempt.id)
    oracle = tmp_path / 'oracle'
    (oracle / 'tests').mkdir(parents=True)
    (oracle / 'tests/test_read.py').write_text('def test_read():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['tests/test_read.py::test_read'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    profile = registry.register_profile(owner, task.project_id,
        VerificationProfileDraft(title='Owner criteria v1', checks=checks, acceptance=task.acceptance), oracle_root=oracle)
    request = VerificationPlanDraft(subject_ids={stage: subject.id for stage in ('baseline', 'target', 'regression', 'integration')},
        profile_id=profile.id, profile_version=1, budget={'max_tool_calls': 12, 'max_attempts': 3, 'wall_seconds': 900.},
        idempotency_key='old-plan')
    first = registry.create_plan(owner, request)
    newer = registry.register_profile(owner, task.project_id, VerificationProfileDraft(title='Owner criteria v2',
        checks=checks, acceptance=changed(task.acceptance, version=2)), oracle_root=oracle)
    assert registry.create_plan(owner, request).model_dump(mode='json') == first.model_dump(mode='json')
    with pytest.raises(StoreError, match='verification_profile_changed'):
        VerificationControl(store).command(owner, first.id, VerificationCommand(operation='start',
            expected_revision=1, idempotency_key='stale-start'))
    with pytest.raises(StoreError, match='contract_changed'):
        registry.create_plan(owner, changed(request, profile_id=newer.id, idempotency_key='criteria-mismatch'))
    assert not store.db.execute('SELECT 1 FROM verification_executions').fetchone()


@pytest.mark.parametrize('supersede', [False, True], ids=['current-profile', 'approval-publication-race'])
def test_registered_host_executes_imported_acceptance_and_rechecks_final_approval(
        workspaced, tmp_path, monkeypatch, supersede):
    from flycode.domain.verification import CheckSpec
    from flycode.domain.verification_control import (
        VerificationCommand,
        VerificationPlanDraft,
        VerificationProfileDraft,
    )
    from flycode.orchestration.host import Host
    from flycode.verification.control import VerificationControl
    from flycode.verification.execution import VerificationExecutor

    store, owner, runtime, project, _, task, attempt, _, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    subject = registry.register_workspace_subject(owner, attempt.id)
    oracle = tmp_path / 'oracle'
    (oracle / 'tests').mkdir(parents=True)
    (oracle / 'tests/test_read.py').write_text('def test_read():\n    assert True\n')
    checks = [CheckSpec(id=stage, stage=stage, targets=['tests/test_read.py::test_read'])
        for stage in ('baseline', 'target', 'regression', 'integration')]
    profile = registry.register_profile(owner, project.id,
        VerificationProfileDraft(title='Original criteria', checks=checks, acceptance=task.acceptance), oracle_root=oracle)
    registered = registry.create_plan(owner, VerificationPlanDraft(subject_ids={stage: subject.id for stage in (
        'baseline', 'target', 'regression', 'integration')}, profile_id=profile.id, profile_version=1,
        budget={'max_tool_calls': 12, 'max_attempts': 3, 'wall_seconds': 900.}, idempotency_key='execution-plan'))
    from flycode.verification.projection import VerificationViews

    view = VerificationViews(store).plan(owner, registered.id).model_dump(mode='json')
    assert view.get('acceptance') == task.acceptance.model_dump(mode='json')
    assert view.get('workflow_id') == task.workflow_id and view.get('task_id') == task.id
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    execution = VerificationControl(store).command(owner, registered.id, VerificationCommand(operation='start',
        expected_revision=1, idempotency_key='start-imported'))
    with Host(store.path, tmp_path / 'host-state') as host:
        connection = host.connect()
        try:
            assigned = VerificationControl(connection).claim(runtime, execution.id)
            worker = VerificationExecutor(connection, assigned.id, assigned.fence)
            original = worker._current_source

            def superseded(registry, record, project):
                result = original(registry, record, project)
                registry.register_profile(owner, project.id, VerificationProfileDraft(title='New criteria', checks=checks,
                    acceptance=changed(task.acceptance, version=2)), oracle_root=oracle)
                return result

            if supersede:
                monkeypatch.setattr(worker, '_current_source', superseded)
            result = worker.run()
            assert result.state == 'completed' and result.verdict.status == 'passed', result
            assert len(result.invocations) == 4
            assert result.current is (not supersede)
            assert result.error == ('verification_profile_changed' if supersede else None)
        finally:
            connection.close()


@pytest.mark.parametrize(('workspaced', 'order', 'standalone'), [
    ({'instances': 1}, 'verification', False),
    ({'instances': 1}, 'agent', False),
    ({'instances': 4}, 'verification', True),
], indirect=['workspaced'], ids=['workflow-check-first', 'workflow-agent-first', 'standalone-global-capacity'])
def test_registered_checks_and_agent_claims_share_global_and_workflow_capacity(workspaced, tmp_path, order, standalone):
    from flycode.domain.orchestration import BudgetAmount, ClaimRequest, TaskCommand
    from flycode.domain.verification_control import (
        VerificationCommand,
        VerificationPlanDraft,
        VerificationProfileDraft,
    )
    from flycode.orchestration.host import Host
    from flycode.verification.control import VerificationControl
    from flycode.verification.execution import VerificationExecutor

    store, owner, runtime, project, _, task, attempt, _, _, _, _ = workspaced
    registry = VerificationRegistry(store)
    subject = (registry.register_subject(owner, project.id) if standalone
        else registry.register_workspace_subject(owner, attempt.id))
    profile = registry.register_profile(owner, project.id, VerificationProfileDraft(title='Registered suite'))
    store.confirm_stopped(runtime, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
    checks = []
    for number in range(4 if standalone else 1):
        registered = registry.create_plan(owner, VerificationPlanDraft(subject_ids={'target': subject.id},
            profile_id=profile.id, profile_version=1, budget={'max_tool_calls': 3, 'max_attempts': 3, 'wall_seconds': 900.},
            idempotency_key='shared-plan-' + str(number)))
        checks.append(VerificationControl(store).command(owner, registered.id, VerificationCommand(operation='start',
            expected_revision=1, idempotency_key='shared-start-' + str(number))))
    with Host(store.path, tmp_path / 'host-state') as host:
        connection = host.connect(catalogue=store.catalogue)
        try:
            control = VerificationControl(connection)
            ready = connection.command(owner, task.id, TaskCommand(operation='retry',
                expected_revision=connection.task(owner, task.id).revision, idempotency_key='retry-author'))
            instance = connection._instance(attempt.instance_id)
            request = ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
                task_id=task.id, expected_task_revision=ready.revision, reserve=BudgetAmount(tool_calls=1),
                idempotency_key='second-author')
            if order == 'verification':
                assigned = [control.claim(runtime, check.id) for check in checks]
                result = connection.claim(runtime, request)
                assert result.attempt is None and 'instance_limit' in result.reasons
                for check in assigned:
                    assert VerificationExecutor(connection, check.id, check.fence).run().state == 'completed'
            else:
                result = connection.claim(runtime, request)
                assert result.attempt is not None, result.reasons
                with pytest.raises(StoreError, match='instance_limit'):
                    control.claim(runtime, checks[0].id)
                connection.confirm_stopped(runtime, result.attempt.id, result.attempt.fence,
                    outcome='failed', expenditure_confirmed=True)
        finally:
            connection.close()


@pytest.fixture
def patched(tmp_path, model_server, request):
    from test_result_delivery import repair

    from flycode.domain.orchestration import (
        AccessScope,
        AgentProfileDraft,
        BudgetAmount,
        ExecutionPermission,
        TaskCommand,
        TaskDraft,
        WorkflowBudget,
        WorkflowDraft,
    )
    from flycode.domain.verification import AcceptanceContract, AcceptanceCriterion
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.orchestration.artifacts import Artifacts

    parameter = getattr(request, 'param', False)
    options = parameter if isinstance(parameter, dict) else {'git': parameter}
    source = tmp_path / 'owner-project'
    (source / 'src').mkdir(parents=True)
    (source / 'tests').mkdir()
    (source / 'src/example.py').write_text('answer = 0\n')
    (source / 'tests/test_example.py').write_text('import runpy\n'
        'def test_example():\n    assert runpy.run_path("src/example.py")["answer"] == 1\n')
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff user bytes\r\n')
    if options.get('git'):
        _git(source, 'init', '--quiet', '--template=')
        _git(source, 'add', '.')
        _git(source, 'commit', '--quiet', '-m', 'Owner project baseline')
    model_server.respond = lambda request: model_server.completion(
        '{"candidates":[{"tool":"read_file","arguments":{"path":"src/example.py"}}]}')
    catalogue = ModelCatalogue(model_server.url)
    model = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    store = Store(tmp_path / 'native.sqlite', catalogue=catalogue)
    project = Projects(store).register(source, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
    host = owner.model_copy(update={'actor_id': 'host', 'role': 'runtime'})
    permission = ExecutionPermission(approved=True, read_scopes=('.',), write_scopes=('src',),
        tools=('read_file', 'edit_file', 'run_tests'))
    contract = AcceptanceContract(id='repair', version=1, criteria=[AcceptanceCriterion(id='result',
        check_ids=['regression'], expected_case_ids=['tests/test_example.py::test_example'])])
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=project.id, budget=WorkflowBudget(),
        task=TaskDraft(title='Repair source', objective='Repair the source without changing owner tests.',
            acceptance=contract, permissions=permission, budget=BudgetAmount(model_calls=2, tool_calls=3))), key='workflow')
    task = store.task(owner, workflow.root_task_id)
    if options.get('aggregate'):
        store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
            idempotency_key='enqueue-root'))
        task = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', parent_id=task.id,
            objective='Repair only the approved source child task.', acceptance=contract, permissions=permission,
            budget=BudgetAmount(model_calls=2, tool_calls=3)), key='writer-child')
    profile = store.register_profile(owner, AgentProfileDraft(role='developer', model_ref=model.model_ref,
        instructions='Repair only the approved source.', project_ids=(project.id,), permissions=permission, context_limit=8192))
    store.register_instance(host, profile.id)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision, idempotency_key='enqueue'))
    store.start_next(owner, key='select-workflow')
    dispatcher = Dispatcher(store, tmp_path / 'runtime')
    binding = dispatcher.bind(owner, task.id, source, expected_revision=task.revision, key='bind', brain='heuristic_fsm')
    result = dispatcher.select(host, key='assign')
    assert result.attempt is not None, result.reasons
    attempt = result.attempt
    assert attempt.execution_binding_id == binding.id
    owned = store, owner, host, workflow, task, attempt, source
    try:
        runtime = repair(owned, model_server, tmp_path)
        artifact = Artifacts(store, runtime.root).for_attempt(owner, attempt.id)
        yield owned, runtime, artifact
    finally:
        store.close()


@pytest.mark.parametrize('patched', [False, True], indirect=True, ids=['non-git', 'git'])
def test_published_artifact_import_derives_exact_baseline_target_and_owner_scope(patched):
    owned, _, artifact = patched
    store, owner, _, workflow, task, attempt, source = owned
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_artifact_subject'), 'Host-owned artifact subject import is missing'
    baseline = registry.register_artifact_subject(owner, artifact.id, stage='baseline')
    target = registry.register_artifact_subject(owner, artifact.id, stage='target')
    assert baseline.snapshot.subject_hash == artifact.baseline.subject_hash
    assert target.snapshot.subject_hash == artifact.target.subject_hash
    assert target.snapshot.ref.task_id == task.id and target.snapshot.ref.attempt_id == attempt.id
    assert target.snapshot.ref.workflow_id == workflow.id and target.origin.record_hash == artifact.patch_hash
    assert target.origin.source_base == artifact.base.base
    assert target.origin.source_subject_hash == artifact.base.subject_hash
    assert (registry.subject_root(owner, target.id) / 'src/example.py').read_text() == 'answer = 1\n'
    assert (registry.subject_root(owner, baseline.id) / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    foreign = owner.model_copy(update={'project_ids': frozenset({'foreign'})})
    with pytest.raises(StoreError, match='scope'):
        registry.register_artifact_subject(foreign, artifact.id, stage='target')


def test_altered_artifact_cannot_supply_a_verification_subject(patched):
    owned, runtime, artifact = patched
    registry = VerificationRegistry(owned[0])
    assert hasattr(registry, 'register_artifact_subject'), 'Host-owned artifact subject import is missing'
    (runtime.root / 'artifacts' / artifact.id / 'target/src/example.py').write_text('answer = 999\n')
    with pytest.raises(StoreError, match='artifact_integrity'):
        registry.register_artifact_subject(owned[1], artifact.id, stage='target')
    assert not owned[0].db.execute('SELECT 1 FROM verification_subjects').fetchone()


def test_artifact_metadata_change_after_last_read_cannot_publish_stale_origin(patched, monkeypatch):
    owned, _, artifact = patched
    store, owner, *_ = owned
    registry = VerificationRegistry(store)
    unchanged = registry._unchanged

    def change_before_publication(project, storage):
        altered = changed(artifact, result_hash='0' * 64)
        store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (altered.model_dump_json(), artifact.id))
        return unchanged(project, storage)

    monkeypatch.setattr(registry, '_unchanged', change_before_publication)
    with pytest.raises(StoreError, match='verification_subject_scope|artifact_integrity'):
        registry.register_artifact_subject(owner, artifact.id, stage='target')
    assert not store.db.execute('SELECT 1 FROM verification_subjects').fetchone()


def test_owner_acceptance_plan_binds_the_imported_artifact_stages_and_exact_oracle(patched, tmp_path):
    from test_result_delivery import checks

    from flycode.domain.verification_control import (
        VerificationPlanDraft,
        VerificationProfileDraft,
    )
    from flycode.verification.oracle import OracleRegistry

    owned, _, artifact = patched
    store, owner, _, workflow, task, _, source = owned
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_artifact_subject'), 'Host-owned artifact subject import is missing'
    baseline = registry.register_artifact_subject(owner, artifact.id, stage='baseline')
    target = registry.register_artifact_subject(owner, artifact.id, stage='target')
    oracle = tmp_path / 'independent-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    profile = registry.register_profile(owner, task.project_id,
        VerificationProfileDraft(title='Owner repair criteria', checks=checks(), acceptance=task.acceptance), oracle_root=oracle)
    plan = registry.create_plan(owner, VerificationPlanDraft(subject_ids={'baseline': baseline.id,
        'target': target.id, 'regression': target.id, 'integration': target.id}, profile_id=profile.id,
        profile_version=1, budget={'max_tool_calls': 24, 'max_attempts': 3, 'wall_seconds': 900.}, idempotency_key='plan'))
    assert plan.workflow_id == workflow.id and plan.task_id == task.id
    assert plan.plan.scope == 'acceptance-contract' and plan.plan.approval_hash == profile.approval_hash
    approved = OracleRegistry(store.path.with_name(store.path.name + '.verification') / 'oracles' / task.project_id / profile.id)
    assert approved.validate(plan.plan).fingerprint() == profile.approval_hash
    assert all(check.subject_hash == plan.plan.subjects[check.stage].subject_hash for check in plan.plan.checks)
    assert plan.plan.subject_hash == target.snapshot.subject_hash and plan.plan.id == plan.id
    assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 0


@pytest.fixture
def integrated(patched, tmp_path):
    from test_result_delivery import checks, reviewer

    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.verification import VerificationEngine

    owned, runtime, artifact = patched
    store, _, host, _, task, _, source = owned
    oracle = tmp_path / 'integration-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = VerificationEngine(runtime.root / 'verification').oracles.approve(task.acceptance, checks(), oracle)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(owned, [artifact], tools=32 if owned[3].root_task_id != task.id else 24)
    record = Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval, owned[3].root_task_id: approval})
    assert record.state == 'completed' and record.candidate is not None
    return owned, runtime, artifact, record


def test_integration_candidate_import_preserves_merged_bytes_and_checked_task(integrated):
    owned, _, artifact, record = integrated
    store, owner, _, workflow, task, author, source = owned
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_integration_subject'), 'Merged integration subject import is missing'
    subject = registry.register_integration_subject(owner, record.id, task_id=task.id)
    assert subject.snapshot.files == record.candidate.files
    assert subject.snapshot.ref.model_dump() == {'project_id': task.project_id, 'workflow_id': workflow.id,
        'task_id': task.id, 'attempt_id': author.id}
    assert subject.origin.record_hash == digest(record.model_dump(mode='json'))
    assert subject.origin.checked_task_id == task.id
    assert subject.snapshot.subject_hash != record.candidate.subject_hash  # The host reference is part of the identity.
    assert artifact.id in record.artifact_hashes
    assert (registry.subject_root(owner, subject.id) / 'src/example.py').read_text() == 'answer = 1\n'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'


@pytest.mark.parametrize(('stage', 'text'), [('baseline', 'answer = 0\n'), ('target', 'answer = 1\n')])
def test_trusted_cli_imports_pinned_artifact_stage_without_new_inference(patched, stage, text):
    owned, _, artifact = patched
    store, owner, _, _, task, _, source = owned
    before = tuple(store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        for table in ('model_calls', 'tool_calls', 'workers'))
    identity = cli_subject(store, source, '--artifact', artifact.id, '--stage', stage)
    registry = VerificationRegistry(store)
    subject = registry.subject(owner, identity)
    assert subject.snapshot.ref.task_id == task.id and subject.origin.record_id == artifact.id
    assert (registry.subject_root(owner, identity) / 'src/example.py').read_text() == text
    assert tuple(store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        for table in ('model_calls', 'tool_calls', 'workers')) == before


def test_trusted_cli_imports_merged_candidate_bound_to_the_checked_task(integrated):
    owned, _, _, record = integrated
    store, owner, _, _, task, _, source = owned
    before = tuple(store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        for table in ('model_calls', 'tool_calls', 'workers'))
    identity = cli_subject(store, source, '--integration', record.id, '--checked-task', task.id, '--stage', 'integration')
    registry = VerificationRegistry(store)
    subject = registry.subject(owner, identity)
    assert subject.snapshot.ref.task_id == task.id and subject.origin.checked_task_id == task.id
    assert subject.snapshot.files == record.candidate.files
    assert (registry.subject_root(owner, identity) / 'src/example.py').read_text() == 'answer = 1\n'
    assert tuple(store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        for table in ('model_calls', 'tool_calls', 'workers')) == before


@pytest.mark.parametrize('change', ['candidate', 'membership', 'checked-task', 'integrator-fence',
    'integrator-attempt', 'reviewer-attempt'])
def test_integration_import_rejects_changed_candidate_or_foreign_provenance(integrated, change):
    owned, runtime, _, record = integrated
    store, owner, _, _, task, _, _ = owned
    registry = VerificationRegistry(store)
    assert hasattr(registry, 'register_integration_subject'), 'Merged integration subject import is missing'
    task_id = task.id
    if change == 'candidate':
        (runtime.root / 'integrations' / record.id / 'candidate/src/example.py').write_text('answer = 99\n')
    elif change == 'membership':
        altered = changed(record, artifact_hashes={identity: '0' * 64 for identity in record.artifact_hashes})
        store.db.execute('UPDATE integrations SET data=? WHERE id=?', (altered.model_dump_json(), record.id))
    elif change == 'checked-task':
        task_id = 'f' * 32
    elif change == 'reviewer-attempt':
        from flycode.domain.results import ReviewRecord

        review = store._load('reviews', record.review_ids[0], ReviewRecord)
        altered = changed(record, attempt_id=review.reviewer_attempt_id)
        store.db.execute('UPDATE integrations SET data=? WHERE id=?', (altered.model_dump_json(), record.id))
    else:
        altered = changed(record, **({'fence': record.fence + 1} if change == 'integrator-fence' else {'attempt_id': owned[5].id}))
        store.db.execute('UPDATE integrations SET data=? WHERE id=?', (altered.model_dump_json(), record.id))
    with pytest.raises(StoreError, match='verification_subject|artifact_integrity|not_found'):
        registry.register_integration_subject(owner, record.id, task_id=task_id)
    assert not store.db.execute('SELECT 1 FROM verification_subjects').fetchone()


@pytest.mark.parametrize('patched', [{'aggregate': True}], indirect=True)
def test_aggregate_root_imports_all_stages_with_integrator_owned_reference(integrated, tmp_path):
    from test_result_delivery import checks

    from flycode.domain.verification_control import (
        VerificationPlanDraft,
        VerificationProfileDraft,
    )

    owned, _, artifact, record = integrated
    store, owner, _, workflow, writer, _, source = owned
    root = store.task(owner, workflow.root_task_id)
    assert root.id != writer.id and root.id not in record.author_attempts
    registry = VerificationRegistry(store)
    subjects = {stage: registry.register_integration_subject(owner, record.id, task_id=root.id, stage=stage)
        for stage in ('baseline', 'target', 'regression', 'integration')}
    for subject in subjects.values():
        assert subject.snapshot.ref.task_id == root.id and subject.snapshot.ref.attempt_id == record.attempt_id
    assert subjects['baseline'].snapshot.files == artifact.baseline.files
    assert subjects['target'].snapshot.files == record.candidate.files
    oracle = tmp_path / 'aggregate-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    profile = registry.register_profile(owner, root.project_id,
        VerificationProfileDraft(title='Aggregate owner checks', checks=checks(), acceptance=root.acceptance), oracle_root=oracle)
    plan = registry.create_plan(owner, VerificationPlanDraft(subject_ids={stage: item.id for stage, item in subjects.items()},
        profile_id=profile.id, profile_version=1, budget={'max_tool_calls': 24, 'max_attempts': 3, 'wall_seconds': 900.},
        idempotency_key='aggregate-plan'))
    assert plan.task_id == root.id and plan.plan.subjects['integration'].ref.attempt_id == record.attempt_id


def test_fresh_reintegration_import_retains_the_original_writer_binding(integrated):
    from test_result_delivery import reviewer

    from flycode.domain.orchestration import TaskCommand
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    owned, runtime, artifact, record = integrated
    store, owner, host, workflow, author, writer, _ = owned
    original_binding = writer.execution_binding_id
    current = store.task(owner, author.id)
    opened = store.command(owner, author.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen'))
    store.command(owner, author.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-again'))
    store.start_next(owner, key='restart')
    approval = record.approval_hashes[author.id]
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(owned, [artifact])
    renewed = Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={author.id: approval, integrator.task_id: approval}, previous_result_id=record.id)
    assert renewed.state == 'completed' and renewed.previous_result_id == record.id
    registry = VerificationRegistry(store)
    subject = registry.register_integration_subject(owner, renewed.id, task_id=author.id)
    assert subject.snapshot.ref.attempt_id == writer.id
    assert store._attempt(writer.id).execution_binding_id == original_binding
    assert store.workflow(owner, workflow.id).semantic_revision > writer.workflow_semantic_revision
