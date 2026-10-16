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


def reviewer(owned, artifacts, *, tools=24, parent_id=None):
    store, owner, host, workflow, author, attempt, _ = owned
    permission = ExecutionPermission(approved=True, read_scopes=('.',), tools=('run_tests',))
    profile = store.register_profile(owner, AgentProfileDraft(role='reviewer',
        model_ref=attempt.profile_snapshot.model_ref, instructions='Verify the exact approved artifact.',
        project_ids=(author.project_id,), permissions=permission, context_limit=8192))
    task = store.create_task(owner, workflow.id, TaskDraft(title='Verify repair', kind='review',
        objective='Independently verify the exact repair with the approved oracle.', parent_id=parent_id or author.id,
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


@pytest.fixture
def completed(owned, delivery):
    from flycode.orchestration.integration import Integration

    store, _, host, _, task, _, _ = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    service = Integration(store, runtime.root)
    result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    return service, result, integrator


@pytest.fixture
def multi_writer(owned, model_server, tmp_path, request):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.runtime import Runtime
    from flycode.verification import VerificationEngine

    store, owner, host, workflow, task, author, source = owned
    mode = getattr(request, 'param', 'overlap')
    overlap = mode in {'overlap', 'large'}
    comparison = '== 1' if mode == 'independent' else '>= 1'
    expression = 'runpy.run_path("src/example.py")["answer"] > 0' if overlap else (
        f'runpy.run_path("src/example.py")["answer"] + runpy.run_path("src/other.py")["answer"] {comparison}')
    (source / 'tests/test_example.py').write_text(f'import runpy\ndef test_example():\n    assert {expression}\n')
    (source / 'src/other.py').write_text('answer = 0\n')
    if mode == 'large':
        (source / 'src/large.txt').write_text('x' * 3501)
    (source / 'user-dirty.bin').write_bytes(b'\x00\xff user bytes\r\n')
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    runtime = Runtime(store, tmp_path / 'runtime')
    engine = VerificationEngine(runtime.root / 'verification')
    approval = engine.oracles.approve(task.acceptance, checks(), oracle)
    artifacts, reviews = [], []
    for index in range(2):
        if index:
            child = store.create_task(owner, workflow.id, TaskDraft(title='Independent second repair',
                objective='Repair the approved example without changing tests.', parent_id=task.id,
                acceptance=task.acceptance, permissions=task.permissions, budget=task.budget), key='second-writer')
            child = store.command(owner, child.id, TaskCommand(operation='enqueue',
                expected_revision=child.revision, idempotency_key='enqueue-second'))
            instance = store.register_instance(host, author.profile_snapshot.id)
            claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
                task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-second'))
            assert claim.attempt is not None, claim.reasons
            active = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
        else:
            active = author
        path = 'src/example.py' if overlap or not index else 'src/other.py'
        value = str(index + 1) if overlap else '1'
        model_server.respond = lambda request, path=path, value=value: model_server.completion(json.dumps({'candidates': [
            {'tool': 'edit_file', 'arguments': {'path': path, 'old_text': '0', 'new_text': value}}]}))
        handle = runtime.launch(host, active.id, active.fence, source, brain='heuristic_fsm')
        try:
            assert handle.finished.wait(15), handle.error
            assert handle.error is None
            assert handle.process_stopped.is_set() and runtime.worker(owner, active.id).stop_confirmed
        finally:
            handle.close()
        artifact = Artifacts(store, runtime.root).for_attempt(owner, active.id)
        assert artifact is not None
        checker = reviewer(owned, [artifact], tools=4)
        review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
        assert review.status == 'approved'
        artifacts.append(artifact)
        reviews.append(review)
    assert artifacts[0].base.subject_hash == artifacts[1].base.subject_hash
    integrator = reviewer(owned, artifacts, tools=50)
    approvals = {item.task_id: approval for item in artifacts}
    approvals[integrator.task_id] = approval
    return runtime, artifacts, reviews, integrator, approvals


def test_overlapping_reviewed_writers_retain_one_inert_resolution_draft(owned, multi_writer):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import Store, StoreError

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    before = store.budget(owner, workflow.id)
    result = service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
        tuple(item.id for item in reviews), approval_hashes=approvals)
    assert result.state == 'conflict' and result.conflict_paths == ('src/example.py',)
    assert result.artifact_hashes == {item.id: item.patch_hash for item in artifacts}
    assert result.candidate is None and not result.changes and not result.verdicts and not result.applied_verdicts
    draft = store.task(owner, result.resolution_task_id)
    assert draft.state == 'planned' and draft.permissions is None and draft.budget is None
    assert draft.input_artifacts == result.artifact_hashes and not draft.dependencies
    assert draft.base_snapshot_hash == artifacts[0].base.subject_hash
    with pytest.raises(StoreError, match='incomplete'):
        store.command(owner, draft.id, TaskCommand(operation='enqueue', expected_revision=draft.revision,
            idempotency_key='unapproved-resolution'))
    assert store._attempt(integrator.id).stop_confirmed
    assert store.task(owner, integrator.task_id).current_attempt_id is None
    assert store.budget(owner, workflow.id).spent == before.spent
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    restarted = Store(store.path, catalogue=store.catalogue)
    try:
        replay = Integration(restarted, runtime.root).run(host, integrator.id, integrator.fence,
            tuple(item.id for item in artifacts), tuple(item.id for item in reviews), approval_hashes=approvals)
        assert replay.model_dump(mode='json') == result.model_dump(mode='json')
        assert len([event for event in restarted.events(owner, workflow.id) if event.kind == 'integration_conflict']) == 1
        assert len([item for item in restarted.tasks(owner, workflow.id) if item.id == draft.id]) == 1
        assert Integration(restarted, runtime.root).reconcile(host, result.id).model_dump(mode='json') == result.model_dump(mode='json')
    finally:
        restarted.close()
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    for item in artifacts:
        assert service.artifacts.get(owner, item.id) == item
        assert store._attempt(item.attempt_id).state == 'awaiting_review'


@pytest.mark.parametrize('multi_writer', ['independent'], indirect=True)
def test_individually_green_patches_fail_combined_checks_before_source_apply(owned, multi_writer):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    before = store.budget(owner, workflow.id)
    with pytest.raises(StoreError, match='verification_failed'):
        service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
            tuple(item.id for item in reviews), approval_hashes=approvals)
    identity = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (integrator.id,)).fetchone()['id']
    record = service.get(owner, identity)
    assert record.state == 'failed' and record.verdicts and not record.applied_verdicts
    assert all(verdict.status != 'passed' for verdict in record.verdicts.values())
    assert not any(call.tool == 'integrate' for call in Tools(store).calls(owner, workflow.id))
    assert not service.current(owner, record.id)
    assert (source / 'src/example.py').read_text() == (source / 'src/other.py').read_text() == 'answer = 0\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert store.budget(owner, workflow.id).spent.tool_calls > before.spent.tool_calls
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()


@pytest.mark.parametrize('multi_writer', ['compatible'], indirect=True)
def test_two_compatible_writers_reach_current_verified_completion(owned, multi_writer):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, task, _, source = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    record = service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
        tuple(item.id for item in reviews), approval_hashes=approvals)
    assert record.state == 'completed' and service.current(owner, record.id)
    assert set(record.applied_verdicts) == {task.id, artifacts[1].task_id, integrator.task_id}
    assert len([call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == integrator.id]) == 25
    assert (source / 'src/example.py').read_text() == (source / 'src/other.py').read_text() == 'answer = 1\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert all(store._attempt(item.attempt_id).state == 'succeeded' for item in artifacts)
    assert store.workflow(owner, workflow.id).state == 'succeeded'
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()


@pytest.mark.parametrize('invalid', ['review', 'artifact', 'approval_scope', 'approval', 'fence', 'foreign_scope'])
def test_conflict_cannot_create_a_draft_from_unapproved_or_unowned_inputs(owned, multi_writer, invalid):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.integration import Integration

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    scope, fence = host, integrator.fence
    if invalid == 'review':
        verdict = reviews[0].verdict
        (service.engine.data_dir / verdict.evidence_id / 'manifest.json').write_bytes(b'changed evidence')
    elif invalid == 'artifact':
        (service.artifacts.directory / artifacts[0].id / 'target/src/example.py').write_text('answer = 99\n')
    elif invalid == 'approval_scope':
        approvals.pop(artifacts[1].task_id)
    elif invalid == 'approval':
        approvals[integrator.task_id] = '0' * 64
    elif invalid == 'fence':
        fence += 1
    else:
        scope = AccessScope(actor_id='foreign', project_ids={'elsewhere'}, role='runtime')
    before = store.budget(owner, workflow.id), store.tasks(owner, workflow.id)
    with pytest.raises(ValueError):
        service.run(scope, integrator.id, fence, tuple(item.id for item in artifacts),
            tuple(item.id for item in reviews), approval_hashes=approvals)
    assert store.db.execute('SELECT COUNT(*) FROM integrations').fetchone()[0] == 0
    assert (store.budget(owner, workflow.id), store.tasks(owner, workflow.id)) == before


def test_conflict_retains_both_inputs_when_resolution_child_limit_is_reached(owned, multi_writer):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    for _ in range(4):
        store.heartbeat(host, integrator.id, integrator.fence)
        checker = reviewer(owned, artifacts[:1], tools=4)
        review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifacts[0].id,
            approval_hash=approvals[artifacts[0].task_id])
        assert review.status == 'approved'
        store.heartbeat(host, integrator.id, integrator.fence)
    before = store.budget(owner, workflow.id)
    service = Integration(store, runtime.root)
    record = service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
        tuple(item.id for item in reviews), approval_hashes=approvals)
    assert record.state == 'conflict' and record.resolution_task_id is None
    assert record.resolution_blocker == 'children'
    assert record.artifact_hashes == {item.id: item.patch_hash for item in artifacts}
    assert len(store.tasks(owner, workflow.id)) == 9
    assert store.task(owner, integrator.task_id).blocked_reason == 'children'
    assert store.budget(owner, workflow.id).spent == before.spent
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_conflict_draft_and_stop_settlement_roll_back_together(owned, multi_writer, monkeypatch):
    from flycode.orchestration.integration import Integration

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    before = store.tasks(owner, workflow.id), store.budget(owner, workflow.id), store.events(owner, workflow.id)
    settle = store._confirm_stopped

    def interrupted(*args, **kwargs):
        settle(*args, **kwargs)
        raise RuntimeError('interrupted before commit')

    with monkeypatch.context() as patch:
        patch.setattr(store, '_confirm_stopped', interrupted)
        with pytest.raises(RuntimeError, match='before commit'):
            service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
                tuple(item.id for item in reviews), approval_hashes=approvals)
    assert store.db.execute('SELECT COUNT(*) FROM integrations').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM execution_lanes WHERE attempt_id=?', (integrator.id,)).fetchone()[0] == 0
    assert (store.tasks(owner, workflow.id), store.budget(owner, workflow.id), store.events(owner, workflow.id)) == before
    assert store._attempt(integrator.id).state == 'running'
    record = service.run(host, integrator.id, integrator.fence, tuple(item.id for item in artifacts),
        tuple(item.id for item in reviews), approval_hashes=approvals)
    assert record.state == 'conflict' and store.task(owner, record.resolution_task_id).state == 'planned'


def test_two_host_connections_cannot_duplicate_a_conflict_or_its_draft(owned, multi_writer, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import Store, StoreError

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, reviews, integrator, approvals = multi_writer
    boundary = Barrier(2)
    persist = Integration._conflict

    def concurrent(service, scope, record):
        boundary.wait(timeout=10)
        return persist(service, scope, record)

    def run():
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Integration(connection, runtime.root).run(host, integrator.id, integrator.fence,
                tuple(item.id for item in artifacts), tuple(item.id for item in reviews), approval_hashes=approvals)
        except StoreError as error:
            return error.code
        finally:
            connection.close()

    before = len(store.tasks(owner, workflow.id)), store.budget(owner, workflow.id).spent
    with monkeypatch.context() as patch:
        patch.setattr(Integration, '_conflict', concurrent)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
    assert len([item for item in results if not isinstance(item, str)]) == 1
    assert [item for item in results if isinstance(item, str)] == ['fence']
    assert store.db.execute('SELECT COUNT(*) FROM integrations').fetchone()[0] == 1
    assert len(store.tasks(owner, workflow.id)) == before[0] + 1
    assert store.budget(owner, workflow.id).spent == before[1]
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert run().state == 'conflict'


@pytest.fixture
def pending_resolution(owned, multi_writer, request):
    from flycode.orchestration.integration import Integration

    store, owner, host, _, root, first, _ = owned
    runtime, artifacts, reviews, stopped_integrator, approvals = multi_writer
    service = Integration(store, runtime.root)
    conflict = service.run(host, stopped_integrator.id, stopped_integrator.fence,
        tuple(item.id for item in artifacts), tuple(item.id for item in reviews), approval_hashes=approvals)
    draft = store.task(owner, conflict.resolution_task_id)
    permission = root.permissions.model_dump()
    scenario = getattr(request, 'param', None)
    if scenario == 'no_read':
        permission['tools'] = [tool for tool in permission['tools'] if tool != 'read_file']
    if scenario == 'no_write':
        permission['write_scopes'] = []
    if scenario == 'no_scope':
        permission['read_scopes'] = ['tests']
    changes = {'input_artifacts': {**draft.input_artifacts, artifacts[0].id: '0' * 64}} if scenario == 'wrong_pin' else (
        {'base_snapshot_hash': '0' * 64} if scenario == 'wrong_base' else {})
    draft = store.command(owner, draft.id, TaskCommand(operation='revise', expected_revision=draft.revision,
        idempotency_key='approve-resolution', changes={'objective':
            'Read both pinned versions of src/example.py. Set answer to the sum of their answer values. '
            'Preserve owner tests and user files; verify the repair.',
            'acceptance': root.acceptance.model_dump(), 'permissions': permission,
            'budget': BudgetAmount(model_calls=8, tool_calls=12).model_dump(), **changes}))
    ready = store.command(owner, draft.id, TaskCommand(operation='enqueue', expected_revision=draft.revision,
        idempotency_key='enqueue-resolution'))
    instance = store.register_instance(host, first.profile_snapshot.id)
    claimed = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=ready.id, expected_task_revision=ready.revision, reserve=ready.budget, idempotency_key='claim-resolution'))
    assert claimed.attempt is not None, claimed.reasons
    resolver = store.confirm_started(host, claimed.attempt.id, claimed.attempt.fence)
    return runtime, artifacts, conflict, resolver, stopped_integrator, approvals


@pytest.fixture
def resolved_delivery(owned, pending_resolution, model_server):
    import re

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, root, _, source = owned
    runtime, artifacts, conflict, resolver, stopped_integrator, approvals = pending_resolution
    service = Integration(store, runtime.root)
    observed = {}

    def response(request):
        prompt = request['messages'][-1]['content']
        for artifact in artifacts:
            result = re.search(r'\[artifact:' + artifact.id + r' [^\]]+\].*?answer\s*=\s*(\d+)', prompt, re.DOTALL)
            if result:
                observed[artifact.id] = int(result.group(1))
        missing = next((item for item in artifacts if item.id not in observed), None)
        if missing is not None:
            candidate = {'tool': 'read_file', 'arguments': {'artifact_id': missing.id, 'path': 'src/example.py'}}
        else:
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/example.py',
                'old_text': 'answer = 0', 'new_text': 'answer = ' + str(sum(observed.values()))}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))

    model_server.respond = response
    before = store.budget(owner, workflow.id)
    deadline = store.workflow(owner, workflow.id).deadline_at
    handle = runtime.launch(host, resolver.id, resolver.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None
        assert handle.process_stopped.is_set()
    finally:
        handle.close()
    assert observed == {artifacts[0].id: 1, artifacts[1].id: 2}
    published = Artifacts(store, runtime.root).for_attempt(owner, resolver.id)
    assert (runtime.root / 'artifacts' / published.id / 'target/src/example.py').read_text() == 'answer = 3\n'
    reads = [call for call in Tools(store).calls(owner, workflow.id)
        if call.attempt_id == resolver.id and call.execution_profile == 'artifact-read-v1']
    assert len(reads) == 2 and all(call.status == 'completed' for call in reads)
    assert all(store._attempt(item.attempt_id).state == 'interrupted' for item in artifacts)
    history = [store._attempt(item.attempt_id) for item in artifacts] + [store._attempt(stopped_integrator.id)]
    checker = reviewer(owned, [published], tools=4)
    approval = approvals[root.id]
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, published.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(owned, [published], tools=50)
    required = {root.id, artifacts[1].task_id, stopped_integrator.task_id, resolver.task_id, integrator.task_id}
    result = service.run(host, integrator.id, integrator.fence, (published.id,), (review.id,),
        approval_hashes={identity: approval for identity in required})
    assert result.state == 'completed' and service.current(owner, result.id)
    assert result.resolved_conflicts == (conflict.id,)
    assert set(result.artifact_hashes) == {published.id}
    assert set(result.applied_verdicts) == required
    assert all(store.task(owner, identity).state == 'succeeded' for identity in required)
    assert history == [store._attempt(item.attempt_id) for item in artifacts] + [store._attempt(stopped_integrator.id)]
    assert store.budget(owner, workflow.id).spent.model_calls > before.spent.model_calls
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert (source / 'src/example.py').read_text() == 'answer = 3\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert store.workflow(owner, workflow.id).deadline_at == deadline
    current = store.task(owner, root.id)
    store.command(owner, current.id, TaskCommand(operation='set_metadata', expected_revision=current.revision,
        idempotency_key='resolved-title', changes={'title': 'Resolved and verified'}))
    assert service.current(owner, result.id)
    return runtime, artifacts, conflict, published, result, approval, history


def test_resolution_worker_uses_both_versions_and_fulfills_original_requirements(resolved_delivery):
    assert resolved_delivery[4].state == 'completed'


def test_resolved_root_reopen_requires_fresh_checks_and_preserves_history(owned, resolved_delivery):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, root, _, source = owned
    runtime, artifacts, conflict, published, previous, approval, history = resolved_delivery
    service = Integration(store, runtime.root)
    before = store.budget(owner, workflow.id)
    deadline = store.workflow(owner, workflow.id).deadline_at
    current = store.task(owner, root.id)
    opened = store.command(owner, root.id, TaskCommand(operation='reopen', expected_revision=current.revision,
        idempotency_key='reopen-resolved-root'))
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=opened.revision,
        idempotency_key='enqueue-resolved-root'))
    assert store.start_next(owner, key='restart-resolved-root').id == workflow.id
    checker = reviewer(owned, [published], tools=4, parent_id=published.task_id)
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, published.id, approval_hash=approval)
    assert review.status == 'approved' and review.id not in previous.review_ids
    integrator = reviewer(owned, [published], tools=40, parent_id=published.task_id)
    required = set(previous.applied_verdicts) - {store._attempt(previous.attempt_id).task_id} | {integrator.task_id}
    old_review = service.reviews.get(owner, conflict.review_ids[0])
    manifest = service.engine.data_dir / old_review.verdict.evidence_id / 'manifest.json'
    retained = manifest.read_bytes()
    snapshot = store.budget(owner, workflow.id), store.tasks(owner, workflow.id)
    try:
        manifest.write_bytes(b'changed original review evidence')
        with pytest.raises(ValueError):
            service.run(host, integrator.id, integrator.fence, (published.id,), (review.id,),
                approval_hashes={identity: approval for identity in required}, previous_result_id=previous.id)
        assert (store.budget(owner, workflow.id), store.tasks(owner, workflow.id)) == snapshot
        assert store.db.execute('SELECT COUNT(*) FROM integrations WHERE attempt_id=?', (integrator.id,)).fetchone()[0] == 0
    finally:
        manifest.write_bytes(retained)
    renewed = service.run(host, integrator.id, integrator.fence, (published.id,), (review.id,),
        approval_hashes={identity: approval for identity in required}, previous_result_id=previous.id)
    assert renewed.state == 'completed' and service.current(owner, renewed.id)
    assert not service.current(owner, previous.id) and service.get(owner, previous.id).state == 'invalidated'
    assert renewed.resolved_conflicts == (conflict.id,) and set(renewed.applied_verdicts) == required
    assert renewed.semantic_revisions[root.id] == previous.semantic_revisions[root.id] + 1
    assert all(store.task(owner, identity).result_id == renewed.id for identity in renewed.contracts)
    assert history == [store._attempt(item.attempt_id) for item in artifacts] + [store._attempt(conflict.attempt_id)]
    assert store.budget(owner, workflow.id).spent.tool_calls == before.spent.tool_calls + 44
    assert store.budget(owner, workflow.id).reserved == BudgetAmount()
    assert store.workflow(owner, workflow.id).deadline_at == deadline
    assert (source / 'src/example.py').read_text() == 'answer = 3\n'
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_completed_resolution_requires_current_original_patch_and_review(owned, resolved_delivery):
    from flycode.orchestration.integration import Integration

    store, owner, _, workflow, _, _, _ = owned
    runtime, artifacts, conflict, _, result, _, _ = resolved_delivery
    service = Integration(store, runtime.root)
    review = service.reviews.get(owner, conflict.review_ids[0])
    paths = [service.artifacts.directory / artifacts[0].id / 'target/src/example.py',
        service.engine.data_dir / review.verdict.evidence_id / 'manifest.json']
    before = store.budget(owner, workflow.id), store.attempts(owner, workflow.id)
    for path in paths:
        retained = path.read_bytes()
        try:
            path.write_bytes(b'changed original input')
            assert not service.current(owner, result.id)
        finally:
            path.write_bytes(retained)
        assert service.current(owner, result.id)
    assert (store.budget(owner, workflow.id), store.attempts(owner, workflow.id)) == before


@pytest.mark.parametrize('pending_resolution', ['wrong_pin', 'wrong_base', 'no_read', 'no_write'], indirect=True)
def test_resolution_rejects_changed_pins_and_missing_permissions_before_worker(owned, pending_resolution):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, conflict, resolver, _, _ = pending_resolution
    before = store.budget(owner, workflow.id), [store._attempt(item.attempt_id) for item in artifacts]
    with pytest.raises(StoreError):
        runtime.launch(host, resolver.id, resolver.fence, source, brain='heuristic_fsm')
    assert (store.budget(owner, workflow.id), [store._attempt(item.attempt_id) for item in artifacts]) == before
    assert not Integration(store, runtime.root).get(owner, conflict.id).resolution_attempts
    assert not store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (resolver.id,)).fetchone()


@pytest.mark.parametrize('arguments', [
    {'artifact_id': None, 'path': 'src/example.py'},
    {'artifact_id': '', 'path': 'src/example.py'},
    {'artifact_id': '0' * 32, 'path': 'src/example.py'},
    {'path': '../baseline/src/example.py'},
    {'path': '/etc/passwd'},
    {'path': '.env'},
    {'path': 'src/example.py', 'stage': 'baseline'},
    {'path': 'src/example.py', 'root': '/'},
])
def test_pinned_read_rejects_unapproved_selectors_and_paths_without_spending(owned, pending_resolution, arguments):
    from flycode.domain import ToolAction
    from flycode.orchestration.artifacts import Artifacts

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    before = store.budget(owner, workflow.id)
    result = Artifacts(store, runtime.root).read(host, resolver.id, resolver.fence,
        ToolAction(id='denied-input', tool='read_file', arguments={'artifact_id': artifacts[0].id, **arguments}))
    assert not result.success and 'content' not in result.payload and not result.stdout
    assert store.budget(owner, workflow.id) == before
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('pending_resolution', ['no_scope'], indirect=True)
def test_pinned_read_enforces_owner_read_scopes(owned, pending_resolution):
    from flycode.domain import ToolAction
    from flycode.orchestration.artifacts import Artifacts

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    before = store.budget(owner, workflow.id)
    result = Artifacts(store, runtime.root).read(host, resolver.id, resolver.fence,
        ToolAction(id='outside-input-scope', tool='read_file', arguments={'artifact_id': artifacts[0].id, 'path': 'src/example.py'}))
    assert not result.success and result.error_message == 'permission' and 'content' not in result.payload
    assert store.budget(owner, workflow.id) == before


async def test_pinned_reads_debit_once_reject_replay_and_survive_local_edit_in_memory(owned, pending_resolution):
    from flycode.agent import _update_working_memory
    from flycode.domain import TaskSpec, ToolAction
    from flycode.memory.working import WorkingMemory
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.environment import AttemptEnvironment

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    service = Artifacts(store, runtime.root)
    runtime.workspaces.prepare(host, resolver.id, resolver.fence, source)
    repository = runtime.workspaces.repository(owner, resolver.id)
    env = AttemptEnvironment(repository, store, host, resolver.id, resolver.fence,
        artifact_executor=lambda action: service.read(host, resolver.id, resolver.fence, action))
    await env.reset(TaskSpec(id=resolver.task_id, task='Read both pinned inputs and edit the copy.'))
    memory = WorkingMemory()
    before = store.budget(owner, workflow.id).spent.tool_calls
    local = ToolAction(id='local-read', tool='read_file', arguments={'path': 'src/example.py'})
    _update_working_memory(memory, local, await env.step(local))
    for index, artifact in enumerate(artifacts):
        action = ToolAction(id='input-' + str(index), tool='read_file', arguments={'artifact_id': artifact.id, 'path': 'src/example.py'})
        transition = await env.step(action)
        assert transition.success and transition.payload['content'] == f'answer = {index + 1}\n'
        _update_working_memory(memory, action, transition)
    assert store.budget(owner, workflow.id).spent.tool_calls == before + 3
    replay = service.read(host, resolver.id, resolver.fence, action)
    assert not replay.success and replay.error_message == 'request_replay'
    assert store.budget(owner, workflow.id).spent.tool_calls == before + 3
    edit = ToolAction(id='local-edit', tool='edit_file', arguments={'path': 'src/example.py', 'old_text': '0', 'new_text': '3'})
    _update_working_memory(memory, edit, await env.step(edit))
    assert not any(fact.startswith('read_file(src/example.py):') for fact in memory.facts)
    assert all(any(artifact.id in fact and f'answer = {index + 1}' in fact for fact in memory.facts)
        for index, artifact in enumerate(artifacts))
    assert (repository / 'src/example.py').read_text() == 'answer = 3\n'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('multi_writer', ['large'], indirect=True)
def test_immutable_binary_and_oversized_inputs_fail_explicitly_without_clipping(owned, pending_resolution):
    from flycode.domain import ToolAction
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    before = store.budget(owner, workflow.id).spent.tool_calls
    for path in ('user-dirty.bin', 'src/large.txt'):
        result = Artifacts(store, runtime.root).read(host, resolver.id, resolver.fence,
            ToolAction(id='unsupported-' + path, tool='read_file', arguments={'artifact_id': artifacts[0].id, 'path': path}))
        assert not result.success and result.error_message == 'artifact_content' and 'content' not in result.payload
    assert store.budget(owner, workflow.id).spent.tool_calls == before + 2
    assert all(call.status == 'failed' for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == resolver.id)


@pytest.mark.parametrize('change', ['criteria', 'permission'])
def test_resolution_cannot_release_original_authors_after_owner_contract_change(owned, pending_resolution, change):
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, conflict, resolver, _, _ = pending_resolution
    original = store.task(owner, artifacts[1].task_id)
    if change == 'criteria':
        released = store.command(owner, original.id, TaskCommand(operation='release_assignment',
            expected_revision=original.revision, idempotency_key='release-for-new-criteria'))
        store.command(owner, released.id, TaskCommand(operation='revise', expected_revision=released.revision,
            idempotency_key='new-original-criteria', changes={'acceptance': {**released.acceptance.model_dump(), 'version': 2}}))
    else:
        store.command(owner, original.id, TaskCommand(operation='cancel', expected_revision=original.revision,
            idempotency_key='revoke-original-permission'))
    before = store.budget(owner, workflow.id), [store._attempt(item.attempt_id) for item in artifacts]
    with pytest.raises(StoreError):
        runtime.launch(host, resolver.id, resolver.fence, source, brain='heuristic_fsm')
    assert (store.budget(owner, workflow.id), [store._attempt(item.attempt_id) for item in artifacts]) == before
    assert not Integration(store, runtime.root).get(owner, conflict.id).resolution_attempts
    assert not store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (resolver.id,)).fetchone()


@pytest.mark.parametrize('condition', ['context_budget', 'input_changed_during_http'])
def test_required_input_context_cannot_be_dropped_or_used_after_tampering(owned, pending_resolution, model_server, condition):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, _, _, source = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    context = CortexContext(task='Read both immutable inputs.', behavior_mode='explore',
        observation=Observation(task='Resolve the patch conflict.'),
        working_memory=['[artifact:' + artifacts[0].id + ' patch=' + artifacts[0].patch_hash + '] ' + 'x' * 100000]
            if condition == 'context_budget' else [])
    cortex = AttemptCortex(store, host, resolver.id, resolver.fence, artifact_root=runtime.root)
    before = store.budget(owner, workflow.id).spent.model_calls, len(model_server.requests)
    if condition == 'context_budget':
        with pytest.raises(StoreError, match='artifact_context_budget'):
            cortex.generate_candidates(context)
        assert (store.budget(owner, workflow.id).spent.model_calls, len(model_server.requests)) == before
        assert any(event.kind == 'artifact_context_blocked' for event in store.events(owner, workflow.id))
    else:
        def changed(request):
            (runtime.root / 'artifacts' / artifacts[0].id / 'target/src/example.py').write_text('answer = 99\n')
            return model_server.completion(json.dumps({'candidates': [
                {'tool': 'edit_file', 'arguments': {'path': 'src/example.py', 'old_text': '0', 'new_text': '99'}}]}))
        model_server.respond = changed
        assert cortex.generate_candidates(context) == []
        assert cortex.last_routing.error == 'artifact_context_stale'
        assert store.budget(owner, workflow.id).spent.model_calls == before[0] + 1
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


def test_pinned_tool_contract_and_observations_survive_optional_context_compaction(owned, pending_resolution, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifacts, _, resolver, _, _ = pending_resolution
    facts = [f'[artifact:{item.id} patch={item.patch_hash} file=known] read_file(src/example.py):\nanswer = {index + 1}\n'
        for index, item in enumerate(artifacts)]
    workspace = 'read_file(src/example.py):\nanswer = 0\n'
    failure = 'edit_file(src/example.py): FAILED: old_text not found'
    context = CortexContext(task='Resolve both inputs.', behavior_mode='diagnose',
        observation=Observation(task='Resolve both inputs.', recent_logs=['old test output ' * 3000],
            tests={'status': 'failed', 'failed': 1}),
        working_memory=['old test output ' * 3000, workspace, failure, *facts], episodic_notes=['old lesson ' * 3000])
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'artifact_id': artifacts[0].id, 'path': 'src/example.py'}}]}))
    cortex = AttemptCortex(store, host, resolver.id, resolver.fence, artifact_root=runtime.root)
    before = store.budget(owner, workflow.id).spent.model_calls
    assert cortex.generate_candidates(context)[0].arguments['artifact_id'] == artifacts[0].id
    request = model_server.requests[-1]
    assert 'artifact_id' in request['messages'][0]['content']
    assert all(fact in request['messages'][-1]['content'] for fact in facts)
    assert workspace in request['messages'][-1]['content'] and failure in request['messages'][-1]['content']
    assert 'edit_file.old_text' in request['messages'][0]['content']
    assert '"failed":1' in request['messages'][-1]['content']
    assert store.budget(owner, workflow.id).spent.model_calls == before + 1
    assert any(event.kind == 'artifact_context_compacted' for event in store.events(owner, workflow.id))
    assert context.working_memory == ['old test output ' * 3000, workspace, failure, *facts]
    assert len(context.observation.recent_logs[0]) > 10000


def test_completed_result_is_current_across_metadata_archive_and_replay(owned, delivery, completed):
    store, owner, host, workflow, task, _, _ = owned
    service, result, integrator = completed
    _, artifact, review, approval = delivery
    before = store.budget(owner, workflow.id)
    assert service.current(owner, result.id)
    original = store.task(owner, task.id)
    metadata = store.command(owner, task.id, TaskCommand(operation='set_metadata', expected_revision=original.revision,
        idempotency_key='cosmetic-title', changes={'title': 'Reviewed repair', 'tags': ('checked',)}))
    assert metadata.semantic_revision == original.semantic_revision
    for hidden in (True, False):
        current = store.task(owner, task.id)
        store.command(owner, task.id, TaskCommand(operation='archive', expected_revision=current.revision,
            idempotency_key='archive-'+str(hidden), changes={'archived': hidden}))
        assert service.current(owner, result.id)
    replay = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert replay.id == result.id and service.current(owner, replay.id)
    assert store.budget(owner, workflow.id) == before


@pytest.mark.parametrize('change', ['source', 'untracked', 'criteria', 'evidence', 'patch', 'retained-original'])
def test_completed_result_changes_invalidate_without_refund_or_rewriting_history(owned, completed, change):
    from flycode.domain.verification import AcceptanceContract

    store, owner, _, workflow, task, author, source = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    history = store.attempts(owner, workflow.id)
    if change == 'source':
        (source / 'src/example.py').write_bytes(b'user changed source\n')
    elif change == 'untracked':
        (source / 'new-user-file.bin').write_bytes(b'new user bytes')
    elif change == 'criteria':
        current = store.task(owner, task.id)
        acceptance = AcceptanceContract.model_validate({**current.acceptance.model_dump(), 'version': 2})
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='revise-criteria', changes={'acceptance': acceptance.model_dump()}))
        assert service.get(owner, result.id).state == 'invalidated'
    elif change == 'evidence':
        verdict = result.applied_verdicts[task.id]
        (service.engine.data_dir / verdict.evidence_id / 'manifest.json').write_bytes(b'changed evidence')
    elif change == 'patch':
        artifact_id = next(iter(result.artifact_hashes))
        (service.artifacts.directory / artifact_id / 'target/src/example.py').write_bytes(b'changed patch')
    else:
        (service.root / 'integrations' / result.id / 'recovery/0').write_bytes(b'late user bytes\n')
    assert not service.current(owner, result.id)
    invalidated = service.refresh(owner, result.id)
    assert invalidated.state == 'invalidated'
    assert store.task(owner, task.id).state == 'blocked'
    assert store.task(owner, task.id).current_attempt_id is None
    assert store.workflow(owner, workflow.id).state == 'incomplete'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.budget(owner, workflow.id) == before
    assert store.attempts(owner, workflow.id) == history
    assert store._attempt(author.id).state == 'succeeded'
    assert any(event.kind == 'result_invalidated' for event in store.events(owner, workflow.id))
    assert service.refresh(owner, result.id) == invalidated
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_tracker_refreshes_stale_result_without_model_calls(owned, completed, model_server):
    from flycode.domain.orchestration import AccessScope
    from flycode.tracker.service import TrackerService

    store, _, _, workflow, task, _, source = owned
    _, result, _ = completed
    reader = AccessScope(actor_id='reader', project_ids={'project'}, role='reader')
    before = len(model_server.requests), store.budget(reader, workflow.id)
    (source / 'src/example.py').write_bytes(b'new user bytes\n')
    tracker = TrackerService(store)
    card = tracker.task(reader, task.id)
    assert card.column == 'blocked' and 'result_invalidated' in card.blockers
    assert card.task.result_id == result.id
    assert card.attempt.state == 'succeeded'
    assert all(card.column == 'blocked' for card in tracker.board(reader, workflow.id))
    assert (len(model_server.requests), store.budget(reader, workflow.id)) == before


def test_replay_with_changed_policy_never_reexecutes_completed_integration(owned, delivery, completed):
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, _, _ = owned
    _, artifact, review, approval = delivery
    service, result, integrator = completed
    before = store.budget(owner, workflow.id)
    for arguments in ({'reruns': 2}, {'approval_hashes': {task.id: approval}}, {'fence': integrator.fence + 1}):
        kwargs = {'approval_hashes': {task.id: approval, integrator.task_id: approval}, **arguments}
        fence = kwargs.pop('fence', integrator.fence)
        with pytest.raises(StoreError, match='idempotency'):
            service.run(host, integrator.id, fence, (artifact.id,), (review.id,), **kwargs)
    assert service.current(owner, result.id)
    assert store.budget(owner, workflow.id) == before


def test_result_inspection_cannot_hold_sql_transaction_or_cross_scope(owned, completed):
    from flycode.domain.orchestration import AccessScope
    from flycode.orchestration.store import StoreError

    store, owner, _, _, _, _, _ = owned
    service, result, _ = completed
    foreign = AccessScope(actor_id='foreign', project_ids={'other'}, role='owner')
    with pytest.raises(StoreError, match='scope'):
        service.refresh(foreign, result.id)
    with store.transaction():
        with pytest.raises(RuntimeError, match='transaction'):
            service.current(owner, result.id)
        with pytest.raises(RuntimeError, match='transaction'):
            store.refresh_results(owner)
    assert service.current(owner, result.id)


def test_reopen_retires_exact_old_checks_and_claims_same_task_with_remaining_budget(owned, completed):
    from flycode.orchestration.store import StoreError
    from flycode.tracker.service import TrackerService

    store, owner, host, workflow, task, author, _ = owned
    service, result, _ = completed
    original = store.task(owner, task.id)
    before = store.budget(owner, workflow.id)
    times = store.workflow(owner, workflow.id)
    attempts = store.attempts(owner, workflow.id)
    command = TaskCommand(operation='reopen', expected_revision=original.revision, idempotency_key='reopen-result')
    opened = store.command(owner, task.id, command)
    assert opened.id == task.id and opened.state == 'planned'
    assert opened.semantic_revision == original.semantic_revision + 1
    assert opened.result_id is None
    assert service.get(owner, result.id).state == 'invalidated' and not service.current(owner, result.id)
    assert store.workflow(owner, workflow.id).state == 'draft'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.workflow(owner, workflow.id).started_at == times.started_at
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at
    assert store.attempts(owner, workflow.id) == attempts
    assert store.budget(owner, workflow.id) == before
    assert store.command(owner, task.id, command) == opened
    historical = [item for item in store.tasks(owner, workflow.id) if item.id != task.id]
    assert len(historical) == 2
    assert all(item.retired_by_result == result.id and item.archived for item in historical)
    tracker = TrackerService(store)
    assert [card.task.id for card in tracker.board(owner, workflow.id)] == [task.id]
    assert len(tracker.board(owner, workflow.id, archived=True)) == 3
    for old in historical:
        with pytest.raises(StoreError, match='historical_result'):
            store.command(owner, old.id, TaskCommand(operation='reopen', expected_revision=old.revision,
                idempotency_key='reopen-old-'+old.id))
    ready = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='reenqueue'))
    assert store.start_next(owner, key='restart').id == workflow.id
    instance = store.instance(host, author.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=ready.revision, reserve=BudgetAmount(tool_calls=1), idempotency_key='reclaim'))
    assert claim.attempt is not None, claim.reasons
    assert claim.attempt.task_id == author.task_id and claim.attempt.id != author.id
    assert store.budget(owner, workflow.id).spent == before.spent
    assert len([attempt for attempt in store.attempts(owner, workflow.id) if attempt.task_id == task.id]) == 2


def test_root_reopen_preserves_owner_revised_review_generation(owned, delivery, completed):
    store, owner, _, _, task, _, source = owned
    service, result, _ = completed
    _, _, review, _ = delivery
    (source / 'owner-note.txt').write_text('New owner requirement\n')
    service.refresh(owner, result.id)
    checker = store.task(owner, store._attempt(review.reviewer_attempt_id).task_id)
    revised = store.command(owner, checker.id, TaskCommand(operation='revise', expected_revision=checker.revision,
        idempotency_key='revise-old-review', changes={'objective': 'Verify the new owner requirement as well.'}))
    root = store.task(owner, task.id)
    store.command(owner, root.id, TaskCommand(operation='reopen', expected_revision=root.revision, idempotency_key='reopen-with-new-review'))
    current = store.task(owner, checker.id)
    assert current.retired_by_result is None and not current.archived
    assert current.semantic_revision == revised.semantic_revision
    assert current.objective == revised.objective


@pytest.mark.parametrize('source_changed', [False, True])
def test_reopen_selected_review_requires_fresh_checks_without_reapplying_patch(owned, delivery, completed, source_changed):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, author, source = owned
    service, result, _ = completed
    runtime, artifact, review, approval = delivery
    author = store._attempt(author.id)
    checker = store._attempt(review.reviewer_attempt_id)
    old = store.task(owner, checker.task_id)
    opened = store.command(owner, old.id, TaskCommand(operation='reopen', expected_revision=old.revision, idempotency_key='reopen-checker'))
    assert opened.retired_by_result is None and not opened.archived
    assert opened.semantic_revision == old.semantic_revision + 1
    assert opened.state == 'planned' and opened.result_id is None
    assert store._attempt(checker.id) == checker
    assert store.task(owner, task.id).state == 'planned'
    ready = store.command(owner, opened.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-checker-again'))
    root = store.task(owner, task.id)
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=root.revision, idempotency_key='enqueue-aggregate'))
    assert store.start_next(owner, key='restart-checker').id == workflow.id
    instance = store.instance(host, checker.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=ready.id, expected_task_revision=ready.revision, reserve=BudgetAmount(tool_calls=4), idempotency_key='claim-checker-again'))
    assert claim.attempt is not None, claim.reasons
    assert claim.attempt.task_id == old.id and claim.attempt.id != checker.id
    assert all(item.retired_by_result == result.id for item in store.tasks(owner, workflow.id)
        if item.id not in {task.id, old.id})
    second = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    fresh = Reviews(store, runtime.root).run(host, second.id, second.fence, artifact.id, approval_hash=approval)
    assert fresh.status == 'approved' and fresh.id != review.id
    integrator = reviewer(owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    installed = (source / 'src/example.py').stat().st_ino
    if source_changed:
        (source / 'owner-change.txt').write_text('Preserve this change\n')
        with pytest.raises(StoreError, match='integration_conflict'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert (source / 'owner-change.txt').read_text() == 'Preserve this change\n'
        assert store.budget(owner, workflow.id).spent == before
    else:
        with pytest.raises(StoreError, match='review_changed'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert store.budget(owner, workflow.id).spent == before
        renewed = service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert renewed.id != result.id and renewed.previous_result_id == result.id
        assert service.current(owner, renewed.id) and not service.current(owner, result.id)
        assert service.get(owner, result.id).state == 'invalidated'
        assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls
        assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 16
        assert store.task(owner, old.id).state == 'succeeded'
        assert store.task(owner, task.id).result_id == renewed.id
        replay = service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
        assert replay.model_dump(mode='json') == renewed.model_dump(mode='json')
        assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 16
        with pytest.raises(StoreError, match='idempotency'):
            service.run(host, integrator.id, integrator.fence, (artifact.id,), (fresh.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        original = runtime.root / 'integrations' / result.id / 'recovery/0'
        original.write_bytes(b'late owner write to the retained original\n')
        assert service.refresh(owner, renewed.id).state == 'invalidated'
        assert not service.current(owner, renewed.id)
        assert original.read_bytes() == b'late owner write to the retained original\n'
    assert (source / 'src/example.py').stat().st_ino == installed
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'
    assert store._attempt(author.id) == author
    assert store._attempt(checker.id) == checker


@pytest.mark.parametrize('operation', ['cancel', 'pause'])
def test_reverification_preserves_owner_stop_of_historical_child(owned, model_server, tmp_path, monkeypatch, operation):
    import shutil

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, root, original, source = owned
    store.confirm_stopped(host, original.id, original.fence, outcome='retry_wait', expenditure_confirmed=True)
    child = store.create_task(owner, workflow.id, TaskDraft(title='Child repair', objective=root.objective,
        parent_id=root.id, acceptance=root.acceptance, permissions=root.permissions, budget=root.budget), key='child-for-reverify')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision, idempotency_key='enqueue-child'))
    instance = store.instance(host, original.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-child'))
    assert claim.attempt is not None, claim.reasons
    author = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    child_owned = store, owner, host, workflow, child, author, source
    runtime = repair(child_owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    service = Integration(store, runtime.root)
    oracle = tmp_path / 'owner-oracle'
    shutil.copytree(source / 'tests', oracle / 'tests')
    approval = service.engine.oracles.approve(root.acceptance, checks(), oracle)
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact], tools=32)
    result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval})
    current = store.task(owner, root.id)
    opened = store.command(owner, root.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-root'))
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-root'))
    assert store.start_next(owner, key='restart-root').id == workflow.id
    checker = reviewer(child_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    integrator = reviewer(child_owned, [artifact], tools=32)

    def stop_child():
        current = store.task(owner, child.id)
        store.command(owner, child.id, TaskCommand(operation=operation, expected_revision=current.revision, idempotency_key='stop-child'))

    if operation == 'cancel':
        stop_child()
    else:
        verify = service._verify

        def verify_then_pause(*args, **kwargs):
            record = verify(*args, **kwargs)
            if not kwargs['applied']:
                stop_child()
            return record
        monkeypatch.setattr(service, '_verify', verify_then_pause)
    historical = store._attempt(author.id)
    with pytest.raises(StoreError, match='completion_blocked'):
        service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={root.id: approval, child.id: approval, integrator.task_id: approval}, previous_result_id=result.id)
    assert store.task(owner, child.id).state != 'succeeded'
    assert store.task(owner, child.id).paused is (operation == 'pause')
    assert store._attempt(author.id) == historical
    assert store.workflow(owner, workflow.id).state != 'succeeded'
    assert (source / 'src/example.py').read_text() == 'answer = 1\n'


def test_expired_reopen_cannot_capture_the_only_workflow_slot(owned, completed):
    store, owner, _, workflow, task, _, _ = owned
    times = store.workflow(owner, workflow.id)
    store.clock = lambda: times.deadline_at + 1
    current = store.task(owner, task.id)
    opened = store.command(owner, task.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-expired'))
    store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=opened.revision, idempotency_key='enqueue-expired'))
    assert store.start_next(owner, key='start-expired') is None
    assert store.workflow(owner, workflow.id).state == 'incomplete'
    assert not store.workflow(owner, workflow.id).active_slot
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at


def test_cancel_completed_result_preserves_owner_cancellation(owned, completed):
    store, owner, _, workflow, task, _, _ = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    current = store.task(owner, task.id)
    store.command(owner, task.id, TaskCommand(operation='cancel', expected_revision=current.revision, idempotency_key='cancel-completed'))
    assert service.get(owner, result.id).state == 'invalidated'
    assert store.workflow(owner, workflow.id).state == 'cancelled'
    assert store.task(owner, task.id).state == 'cancelled'
    assert store.budget(owner, workflow.id) == before


def test_reopened_generation_requires_new_real_checks_and_preserves_prior_result(owned, delivery, completed, model_server):
    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, task, author, source = owned
    runtime, _, _, _ = delivery
    service, old_result, _ = completed
    before = store.budget(owner, workflow.id).spent
    times = store.workflow(owner, workflow.id)
    current = store.task(owner, task.id)
    current = store.command(owner, task.id, TaskCommand(operation='reopen', expected_revision=current.revision, idempotency_key='reopen-second-generation'))
    acceptance = {**current.acceptance.model_dump(), 'version': 2}
    current = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
        idempotency_key='owner-extends-task-budget', changes={'acceptance': acceptance, 'budget': {'model_calls': 4, 'tool_calls': 6}}))
    current = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=current.revision, idempotency_key='enqueue-second-generation'))
    store.start_next(owner, key='start-second-generation')
    instance = store.instance(host, author.instance_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=current.revision, reserve=BudgetAmount(model_calls=1, tool_calls=1), idempotency_key='claim-second-generation'))
    assert claim.attempt is not None, claim.reasons
    second = store.confirm_started(host, claim.attempt.id, claim.attempt.fence)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}]}))
    handle = runtime.launch(host, second.id, second.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(15), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
    finally:
        handle.close()
    artifact = Artifacts(store, runtime.root).for_attempt(owner, second.id)
    assert artifact.changes == ()
    assert not service.current(owner, old_result.id)
    revised = store.task(owner, task.id)
    new_owned = store, owner, host, workflow, revised, second, source
    # The already repaired baseline now must pass; its owner-approved policy
    # belongs to acceptance version2 and cannot reuse the old green verdict.
    # Use the unchanged fixture oracle retained by the delivery fixture.
    oracle = runtime.root.parent / 'owner-oracle'
    specs = [check.model_copy(update={'expected_outcome': 'passed'}) for check in checks()]
    approval = service.engine.oracles.approve(revised.acceptance, specs, oracle)
    assert approval != old_result.approval_hashes[task.id]
    checker = reviewer(new_owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(new_owned, [artifact])
    new_result = service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert new_result.id != old_result.id and service.current(owner, new_result.id)
    assert service.get(owner, old_result.id).state == 'invalidated'
    assert store.task(owner, task.id).result_id == new_result.id
    assert store._attempt(author.id).state == store._attempt(second.id).state == 'succeeded'
    assert store.workflow(owner, workflow.id).deadline_at == times.deadline_at
    assert store.budget(owner, workflow.id).spent.model_calls == before.model_calls + 1
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 22
    assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_two_connections_invalidate_once_and_restart_retains_fact(owned, completed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.integration import Integration
    from flycode.orchestration.store import Store
    from flycode.tracker.service import TrackerService

    store, owner, _, workflow, task, _, source = owned
    service, result, _ = completed
    before = store.budget(owner, workflow.id)
    (source / 'src/example.py').write_bytes(b'user concurrent update\n')
    barrier = Barrier(2)

    def refresh():
        other = Store(store.path)
        try:
            checker = Integration(other, service.root)
            barrier.wait(timeout=5)
            return checker.refresh(owner, result.id)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh(), range(2)))
    assert all(record.state == 'invalidated' for record in results)
    events = [event for event in store.events(owner, workflow.id) if event.kind == 'result_invalidated']
    assert len(events) == len(result.contracts)
    restarted = Store(store.path)
    try:
        card = TrackerService(restarted).task(owner, task.id)
        assert card.column == 'blocked' and 'result_invalidated' in card.blockers
        assert restarted.budget(owner, workflow.id) == before
        assert len([event for event in restarted.events(owner, workflow.id) if event.kind == 'result_invalidated']) == len(events)
    finally:
        restarted.close()


def test_invalidating_old_result_never_releases_another_active_workflow(owned, completed):
    from flycode.domain.orchestration import WorkflowDraft

    store, owner, _, workflow, task, _, source = owned
    service, result, _ = completed
    other = store.create_workflow(owner, WorkflowDraft(project_id=task.project_id, task=TaskDraft(
        title='Next explicit task', objective=task.objective, acceptance=task.acceptance, permissions=task.permissions,
        budget=task.budget), budget=workflow.budget), key='other-workflow')
    root = store.task(owner, other.root_task_id)
    store.command(owner, root.id, TaskCommand(operation='enqueue', expected_revision=root.revision, idempotency_key='enqueue-other'))
    assert store.start_next(owner, key='start-other').id == other.id
    before = store.workflow(owner, other.id)
    (source / 'src/example.py').write_bytes(b'user changed old project\n')
    assert service.refresh(owner, result.id).state == 'invalidated'
    assert store.workflow(owner, other.id) == before
    assert store.workflow(owner, other.id).active_slot
    assert not store.workflow(owner, workflow.id).active_slot


def test_claim_refreshes_stale_result_before_revision_or_admission(owned, completed):
    from flycode.orchestration.store import StoreError

    store, owner, host, workflow, task, author, source = owned
    _, result, _ = completed
    previous = store.task(owner, task.id)
    instance = store.instance(host, author.instance_id)
    (source / 'src/example.py').write_bytes(b'user changed dependency\n')
    with pytest.raises(StoreError, match='revision'):
        store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=task.id, expected_task_revision=previous.revision, reserve=BudgetAmount(tool_calls=1), idempotency_key='stale-result-claim'))
    assert store.task(owner, task.id).state == 'blocked'
    assert store.task(owner, task.id).result_id == result.id
    assert not store.workflow(owner, workflow.id).active_slot


def test_schema8_result_without_freshness_pins_invalidates_after_restart(owned, completed):
    from flycode.orchestration.store import Store
    from flycode.tracker.service import TrackerService

    store, owner, _, workflow, task, _, _ = owned
    _, result, _ = completed
    before = store.budget(owner, workflow.id)
    attempts = store.attempts(owner, workflow.id)
    legacy = result.model_dump(mode='json', exclude={
        'runtime_root', 'previous_result_id', 'semantic_revisions', 'approval_hashes', 'reruns'})
    store.db.execute('UPDATE integrations SET data=? WHERE id=?', (json.dumps(legacy), result.id))
    store.db.execute('DROP INDEX current_integration')
    store.db.execute('PRAGMA user_version=8')
    restarted = Store(store.path, catalogue=store.catalogue)
    try:
        card = TrackerService(restarted).task(owner, task.id)
        assert card.task.state == 'blocked' and card.task.result_id == result.id
        assert restarted.workflow(owner, workflow.id).state == 'incomplete'
        assert not restarted.workflow(owner, workflow.id).active_slot
        assert restarted.budget(owner, workflow.id) == before
        assert restarted.attempts(owner, workflow.id) == attempts
        assert json.loads(restarted.db.execute('SELECT data FROM integrations WHERE id=?', (result.id,)).fetchone()['data'])['state'] == 'invalidated'
    finally:
        restarted.close()


@pytest.mark.parametrize('field', ['result_id', 'retired_by_result', 'blocked_reason'])
def test_owner_command_cannot_supply_host_result_fields(owned, field):
    from flycode.orchestration.store import StoreError

    store, owner, _, _, task, _, _ = owned
    current = store.task(owner, task.id)
    with pytest.raises(StoreError, match='command'):
        store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='forge-'+field, changes={field: 'forged-result'}))
    assert store.task(owner, task.id) == current


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


def test_host_check_process_is_durable_before_pytest_admission(owned, delivery, monkeypatch):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import custody

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    run = custody.run
    admitted = []

    def inspect_admission(*args):
        def admit(ref):
            args[-1](ref)
            assert not store.db.in_transaction
            calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
            assert calls[-1].execution_profile == 'registered-pytest-v1'
            assert calls[-1].process == ref and calls[-1].status == 'running'
            with pytest.raises(BlockingIOError), custody.stopped(ref):
                pytest.fail('An executing registered check had no custody lock')
            admitted.append(ref)
        return run(*args[:-1], admit)

    monkeypatch.setattr(custody, 'run', inspect_admission)
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'approved' and len(admitted) == 4
    assert len({ref.id for ref in admitted}) == 4
    for ref in admitted:
        with custody.stopped(ref) as receipt:
            assert receipt.started and receipt.stop_confirmed


@pytest.mark.parametrize('when', ['before-admission', 'after-start'])
def test_owner_pause_fences_check_admission_and_retains_started_effects(owned, delivery, monkeypatch, when):
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools
    from flycode.verification import custody

    store, owner, host, workflow, _, _, _ = owned
    runtime, artifact, _, approval = delivery
    checker = reviewer(owned, [artifact])
    before = store.budget(owner, workflow.id).spent
    run = custody.run

    def pause():
        current = store.task(owner, checker.task_id)
        store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
            idempotency_key='pause-host-check'))

    def interrupt(*args):
        def admit(ref):
            if when == 'before-admission':
                pause()
            return args[-1](ref)
        def state(value):
            args[-2](value)
            if value == 'running' and when == 'after-start':
                pause()
        return run(*args[:-2], state, admit)

    monkeypatch.setattr(custody, 'run', interrupt)
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'inconclusive'
    calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id]
    assert len(calls) == 1 and calls[0].late
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 1
    if when == 'after-start':
        assert calls[0].status == 'uncertain'
        assert store.task(owner, checker.task_id).current_attempt_id == checker.id
        assert not store._attempt(checker.id).stop_confirmed
        assert store.budget(owner, workflow.id).uncertain
        with custody.stopped(calls[0].process) as receipt:
            assert receipt.started and receipt.stop_confirmed and receipt.reason == 'cancelled'
    else:
        assert calls[0].process is None and calls[0].status == 'failed'
        assert store.task(owner, checker.task_id).current_attempt_id is None
        assert store._attempt(checker.id).stop_confirmed
        assert not store.budget(owner, workflow.id).uncertain


@pytest.mark.parametrize('owner_change', ['retained-original', 'source-replacement'])
def test_actual_integrator_crash_requires_stopped_checks_and_effect_reconciliation(owned, model_server, tmp_path, owner_change):
    import os
    import signal
    import subprocess
    import sys
    import time

    from flycode.orchestration.artifacts import Artifacts
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools
    from flycode.verification.custody import stopped

    store, owner, host, workflow, task, author, source = owned
    runtime = repair(owned, model_server, tmp_path)
    artifact = Artifacts(store, runtime.root).for_attempt(owner, author.id)
    service = Integration(store, runtime.root)
    block, ready, counter = [tmp_path / name for name in ('block-applied', 'check-ready.json', 'external-count.txt')]
    oracle = tmp_path / 'recovery-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_example.py').write_text(
        'import json, os, runpy, subprocess, sys, time\nfrom pathlib import Path\n'
        'def test_example():\n'
        f'    if Path({str(block)!r}).exists():\n'
        f'        with Path({str(counter)!r}).open("a") as stream: stream.write("once\\n")\n'
        '        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        f'        Path({str(ready)!r}).write_text(json.dumps(dict(group=os.getpgrp(), child=child.pid)))\n'
        '        time.sleep(60)\n'
        '    assert runpy.run_path("src/example.py")["answer"] == 1\n')
    approval = service.engine.oracles.approve(task.acceptance, checks(), oracle.parent)
    checker = reviewer(owned, [artifact])
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, artifact.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(owned, [artifact])
    config = tmp_path / 'coordinator.json'
    config.write_text(json.dumps({'database': str(store.path), 'root': str(runtime.root),
        'scope': host.model_dump(mode='json'), 'attempt': integrator.id, 'fence': integrator.fence,
        'artifact': artifact.id, 'review': review.id, 'approval': {task.id: approval, integrator.task_id: approval}, 'block': str(block)}))
    script = tmp_path / 'coordinator.py'
    script.write_text('''import json, sys
from pathlib import Path
from flycode.domain.orchestration import AccessScope
from flycode.orchestration.store import Store
from flycode.orchestration.integration import Integration
config=json.loads(Path(sys.argv[1]).read_text())
store=Store(Path(config['database']))
service=Integration(store,Path(config['root']))
verify=service._verify
def block_applied(*args,**kwargs):
    if kwargs['applied']:
        Path(config['block']).touch()
    return verify(*args,**kwargs)
service._verify=block_applied
service.run(AccessScope.model_validate(config['scope']),config['attempt'],config['fence'],
    (config['artifact'],),(config['review'],),approval_hashes=config['approval'])
''')
    coordinator = None
    group = None
    with (source / 'src/example.py').open('r+b') as original, (tmp_path / 'coordinator.log').open('wb') as log:
        try:
            coordinator = subprocess.Popen([sys.executable, str(script), str(config)], start_new_session=True, stdout=log, stderr=log)
            deadline = time.monotonic() + 20
            while not ready.exists():
                assert coordinator.poll() is None, (tmp_path / 'coordinator.log').read_text()
                assert time.monotonic() < deadline
                time.sleep(.02)
            group = json.loads(ready.read_text())['group']
            record = service.get(owner, store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (integrator.id,)).fetchone()['id'])
            calls = [call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == integrator.id]
            active = [call for call in calls if call.status == 'running']
            assert len(active) == 1 and active[0].process is not None
            os.kill(coordinator.pid, signal.SIGSTOP)
            _, status = os.waitpid(coordinator.pid, os.WUNTRACED)
            assert os.WIFSTOPPED(status)
            store.recover(host)
            before = store.budget(owner, workflow.id)
            with pytest.raises(StoreError, match='coordinator_running'):
                service.reconcile(host, record.id)
            assert store.budget(owner, workflow.id) == before
            assert (source / 'src/example.py').read_text() == 'answer = 1\n'
            coordinator.kill()
            coordinator.wait(timeout=5)
            deadline = time.monotonic() + 10
            while True:
                try:
                    with stopped(active[0].process) as receipt:
                        assert receipt.started and receipt.reason == 'coordinator_lost'
                    break
                except (BlockingIOError, ValueError):
                    assert time.monotonic() < deadline
                    time.sleep(.02)
            pending = service.reconcile(host, record.id)
            assert pending.state == 'reconciling' and pending.error == 'effects_unknown'
            assert (source / 'src/example.py').read_text() == 'answer = 1\n'
            assert store.task(owner, integrator.task_id).current_attempt_id == integrator.id
            assert store.budget(owner, workflow.id).uncertain
            # The harness independently checks the fixture's declared external
            # effect before the trusted host records its resolution. Group exit
            # alone must never perform this acknowledgement.
            with pytest.raises(ProcessLookupError):
                os.killpg(group, 0)
            assert counter.read_text() == 'once\n'
            Tools(store).finish(host, active[0].id, status='failed', error_class='FixtureEffectsReconciled')
            original.write(b'late owner bytes\n')
            original.truncate()
            original.flush()
            if owner_change == 'source-replacement':
                replacement = tmp_path / 'owner-replacement.py'
                replacement.write_bytes(b'new owner source bytes\n')
                replacement.replace(source / 'src/example.py')
            reconciled = service.reconcile(host, record.id)
            assert reconciled.state == ('failed' if owner_change == 'retained-original' else 'reconciling')
            assert reconciled.error == ('host_interrupted' if owner_change == 'retained-original' else 'rollback_conflict')
            assert (source / 'src/example.py').read_bytes() == (
                b'late owner bytes\n' if owner_change == 'retained-original' else b'new owner source bytes\n')
            assert store.budget(owner, workflow.id).spent == before.spent
            assert counter.read_text() == 'once\n'
            assert store.workflow(owner, workflow.id).state != 'succeeded'
            assert store._attempt(integrator.id).stop_confirmed is (owner_change == 'retained-original')
            if owner_change == 'retained-original':
                assert store.task(owner, integrator.task_id).current_attempt_id is None
                assert not store.workflow(owner, workflow.id).active_slot
                assert not store.budget(owner, workflow.id).uncertain
                assert service.reconcile(host, record.id).model_dump(mode='json') == reconciled.model_dump(mode='json')
            else:
                assert store.task(owner, integrator.task_id).current_attempt_id == integrator.id
                with pytest.raises(StoreError, match='reconcile_required'):
                    store.confirm_stopped(host, integrator.id, integrator.fence, outcome='failed', expenditure_confirmed=True)
            assert (source / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
            with pytest.raises(StoreError, match='reconcile_required'):
                service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                    approval_hashes={task.id: approval, integrator.task_id: approval})
        finally:
            if coordinator is not None:
                if coordinator.poll() is None:
                    coordinator.kill()
                coordinator.wait(timeout=5)
            if group is not None:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    from flycode.verification.runner import _group_gone

                    assert _group_gone(group), 'Harness process cleanup remained unconfirmed'


@pytest.mark.parametrize('interruption', ['output_limit', 'descendants_terminated'])
def test_interrupted_check_retains_actual_effects_until_reconciliation(owned, delivery, tmp_path, interruption):
    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools
    from flycode.orchestration.verification import CheckExecutor
    from flycode.verification.custody import stopped
    from flycode.verification.runner import run_pytest

    store, owner, host, workflow, _, _, _ = owned
    _, artifact, _, _ = delivery
    checker = reviewer(owned, [artifact])
    subject = tmp_path / 'noisy-subject'
    subject.mkdir()
    (subject / 'pytest.ini').write_text('[pytest]\naddopts = -s\n')
    effect = tmp_path / 'external-effect'
    if interruption == 'output_limit':
        code = ('import os, time\nfrom pathlib import Path\n'
            f'def test_noisy():\n    Path({str(effect)!r}).write_text("started")\n'
            '    os.write(1, b"x" * (3 * 1024 * 1024))\n    time.sleep(60)\n')
    else:
        child = f'from pathlib import Path; import time; Path({str(effect)!r}).write_text("started"); time.sleep(60)'
        code = ('import subprocess, sys, time\nfrom pathlib import Path\n'
            f'def test_child():\n    subprocess.Popen([sys.executable, "-c", {child!r}])\n'
            '    for _ in range(200):\n'
            f'        if Path({str(effect)!r}).exists(): return\n'
            '        time.sleep(.01)\n    assert False, "Owned child did not start"\n')
    (subject / 'test_noisy.py').write_text(code)
    before = store.budget(owner, workflow.id).spent
    executor = CheckExecutor(store, host, checker.id, checker.fence)
    result = executor.execute(CheckSpec(), 1, lambda **kwargs:
        run_pytest(subject, tmp_path / 'noisy-output', 10, 1, lambda: False, **kwargs))
    call = next(call for call in Tools(store).calls(owner, workflow.id) if call.attempt_id == checker.id)
    assert effect.read_text() == 'started'
    assert not result.complete and result.process_started and result.stop_confirmed
    with stopped(call.process) as receipt:
        assert receipt.reason == interruption
    assert call.status == 'uncertain' and executor.should_stop()
    assert store.budget(owner, workflow.id).spent.tool_calls == before.tool_calls + 1
    with pytest.raises(StoreError, match='resource_busy'):
        store.confirm_stopped(host, checker.id, checker.fence, outcome='failed', expenditure_confirmed=True)


def test_recovery_never_resurrects_an_assignment_stopped_by_another_host(owned, delivery, monkeypatch):
    from contextlib import contextmanager

    from flycode.orchestration.integration import Integration

    store, owner, host, workflow, task, _, _ = owned
    runtime, artifact, review, approval = delivery
    service = Integration(store, runtime.root)
    integrator = reviewer(owned, [artifact])
    confirm = store.confirm_stopped

    def unavailable(*args, **kwargs):
        raise OSError('Host verification could not start')
    def crash(*args, **kwargs):
        raise RuntimeError('Coordinator died before acknowledging stop')
    monkeypatch.setattr(service, '_verify', unavailable)
    monkeypatch.setattr(store, 'confirm_stopped', crash)
    with pytest.raises(RuntimeError, match='before acknowledging stop'):
        service.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, integrator.task_id: approval})
    monkeypatch.setattr(store, 'confirm_stopped', confirm)
    record = service.get(owner, store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (integrator.id,)).fetchone()['id'])
    assert record.state == 'failed' and not store._attempt(integrator.id).stop_confirmed
    before = store.budget(owner, workflow.id).spent
    transaction = store.transaction
    raced = []

    @contextmanager
    def stop_before_transaction():
        if not raced:
            raced.append(True)
            confirm(host, integrator.id, integrator.fence, outcome='failed', expenditure_confirmed=True)
        with transaction():
            yield

    monkeypatch.setattr(store, 'transaction', stop_before_transaction)
    assert service.reconcile(host, record.id).state == 'failed'
    assert raced == [True]
    assert store._attempt(integrator.id).state == 'failed'
    assert store._attempt(integrator.id).stop_confirmed
    assert store.task(owner, integrator.task_id).current_attempt_id is None
    assert store.instance(host, integrator.instance_id).state == 'idle'
    assert store.budget(owner, workflow.id).spent == before
    assert not store.budget(owner, workflow.id).uncertain


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
