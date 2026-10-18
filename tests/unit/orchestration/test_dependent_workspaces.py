"""A declared coding dependency supplies immutable bytes to the next checkout."""

import hashlib
import json
import shutil

import pytest
from test_pipeline_dispatch import pipeline_ready, published_pipeline  # noqa: F401
from test_result_delivery import multi_writer  # noqa: F401
from test_store import setup  # noqa: F401
from test_task_completions import approved_task, finish_author  # noqa: F401

from flycode.domain.orchestration import ClaimRequest, TaskCommand, TaskDraft
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.store import Store, StoreError, changed
from flycode.orchestration.workspaces import Workspaces
from flycode.verification.snapshot import digest


@pytest.fixture
def dependent_ready(approved_task):  # noqa: F811
    prepared, review = approved_task
    store, owner, host, workflow, root, author, _, _, dispatcher, _, _ = prepared
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Continue the checked repair',
        'parent_id': root.id, 'dependencies': (author.id,)}), key='dependent-checkout')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-dependent-checkout'))
    proof = finish_author(prepared, review)
    artifact = Artifacts(store, dispatcher.root).get(owner, proof.artifact_id)
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-dependent-checkout')).attempt
    assert attempt is not None
    try:
        yield prepared, proof, artifact, attempt
    finally:
        if store._attempt(attempt.id).state in {'assigned', 'running', 'stopping', 'reconciling'}:
            store.confirm_stopped(host, attempt.id, attempt.fence, outcome='cancelled', expenditure_confirmed=True)


def test_dependent_checkout_starts_from_exact_checked_predecessor_without_applying_to_owner(dependent_ready):
    prepared, proof, artifact, attempt = dependent_ready
    store, owner, host, workflow, _, author, _, _, dispatcher, source, _ = prepared
    assert attempt.prerequisites[author.id].id == proof.id
    assert attempt.prerequisites[author.id].sha256 == digest(proof.model_dump(mode='json'))
    workspaces = Workspaces(store, dispatcher.root / 'workspaces')
    workspace = workspaces.prepare(host, attempt.id, attempt.fence, source)
    checkout = workspaces.repository(owner, attempt.id)
    assert (checkout / 'src/parser.py').read_text() == 'value = 1\n', 'Dependency output was not composed into the private checkout'
    assert workspace.snapshot.subject_hash == artifact.base.subject_hash
    assert workspace.input_snapshot.files['src/parser.py'] == artifact.target.files['src/parser.py']
    assert workspace.prerequisites == attempt.prerequisites
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    assert (checkout / 'dirty.bin').read_bytes() == (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert Artifacts(store, dispatcher.root).get(owner, artifact.id) == artifact
    assert store.workflow(owner, workflow.id).state == 'running'


@pytest.mark.parametrize('change', ['source', 'source-identity', 'artifact', 'evidence', 'missing-evidence-tree', 'missing-pins', 'foreign-pins'])
def test_changed_prerequisite_cannot_create_a_private_checkout_or_worker(dependent_ready, change):
    prepared, _, artifact, attempt = dependent_ready
    store, owner, host, workflow, _, author, _, _, dispatcher, source, _ = prepared
    workspaces = Workspaces(store, dispatcher.root / 'workspaces')
    before = store.budget(owner, workflow.id)
    if change == 'source':
        (source / 'dirty.bin').write_bytes(b'new owner data')
    elif change == 'source-identity':
        retained = source.with_name('retained-owner-source')
        source.rename(retained)
        shutil.copytree(retained, source)
    elif change == 'artifact':
        (Artifacts(store, dispatcher.root).directory / artifact.id / 'target/src/parser.py').write_text('value = 999\n')
    elif change in {'evidence', 'missing-evidence-tree'}:
        from flycode.orchestration.completions import Completions

        proof = Completions(store, dispatcher.root).get(owner, store.task(owner, author.id).completion_id)
        review = store.db.execute('SELECT data FROM reviews WHERE id=?', (proof.review_id,)).fetchone()
        directory = dispatcher.root / 'verification' / json.loads(review['data'])['verdict']['evidence_id']
        if change == 'evidence':
            (directory / 'manifest.json').unlink()
        else:
            shutil.rmtree(directory)
    else:
        with store.transaction():
            store._save_attempt(changed(attempt, prerequisites={} if change == 'missing-pins' else {
                author.id: {'id': 'f' * 32, 'sha256': '0' * 64}}))
    with pytest.raises(StoreError):
        workspaces.prepare(host, attempt.id, attempt.fence, source)
    assert not store.db.execute('SELECT 1 FROM workspaces WHERE attempt_id=?', (attempt.id,)).fetchone()
    assert not store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt.id,)).fetchone()
    assert not any(path.name.startswith('.') for path in workspaces.root.iterdir())
    assert store.budget(owner, workflow.id) == before


def test_changed_output_during_composition_never_publishes_workspace(dependent_ready, monkeypatch):
    from flycode.orchestration import composition

    prepared, _, artifact, attempt = dependent_ready
    store, _, host, _, _, _, _, _, dispatcher, source, _ = prepared
    write = composition.write_files
    def change_after_write(*args, **kwargs):
        write(*args, **kwargs)
        (Artifacts(store, dispatcher.root).directory / artifact.id / 'target/src/parser.py').write_text('value = 7\n')
    monkeypatch.setattr(composition, 'write_files', change_after_write)
    with pytest.raises(StoreError, match='dependency_changed'):
        Workspaces(store, dispatcher.root / 'workspaces').prepare(host, attempt.id, attempt.fence, source)
    assert not store.db.execute('SELECT 1 FROM workspaces WHERE attempt_id=?', (attempt.id,)).fetchone()
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def test_two_connections_publish_one_pinned_composite_workspace(dependent_ready):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    prepared, _, _, attempt = dependent_ready
    store, owner, host, workflow, _, _, _, _, dispatcher, source, _ = prepared
    barrier = Barrier(2)
    before = store.budget(owner, workflow.id)
    def prepare(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            barrier.wait(timeout=5)
            return Workspaces(connection, dispatcher.root / 'workspaces').prepare(host, attempt.id, attempt.fence, source)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, range(2)))
    assert results[0] == results[1]
    assert sum(event.kind == 'workspace_created' and event.attempt_id == attempt.id for event in store.events(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id) == before
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def test_workspace_and_artifact_without_new_lineage_fields_remain_readable(approved_task):  # noqa: F811
    prepared, _ = approved_task
    store, owner, _, _, _, author, _, _, dispatcher, _, _ = prepared
    artifacts = Artifacts(store, dispatcher.root)
    artifact = artifacts.for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    workspace = artifacts.workspaces.get(owner, artifact.attempt_id)
    legacy_workspace = workspace.model_dump(mode='json', exclude={'prerequisites', 'input_snapshot'})
    raw = json.dumps(legacy_workspace, ensure_ascii=False, separators=(',', ':'))
    (artifacts.workspaces.root / workspace.id / 'workspace.json').write_text(raw)
    store.db.execute('UPDATE workspaces SET data=? WHERE id=?', (raw, workspace.id))
    restored = artifacts.workspaces.get(owner, artifact.attempt_id)
    assert restored.prerequisites == {} and restored.input_snapshot is None
    raw = artifact.model_dump_json(exclude={'patch_hash', 'prerequisites'})
    legacy_artifact = artifact.model_dump(mode='json', exclude={'prerequisites'})
    legacy_artifact['patch_hash'] = hashlib.sha256(raw.encode()).hexdigest()
    (artifacts.directory / artifact.id / 'manifest.json').write_text(raw)
    store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(legacy_artifact), artifact.id))
    assert artifacts.get(owner, artifact.id).patch_hash == legacy_artifact['patch_hash']


@pytest.mark.parametrize('mode', ['chain', 'reverse', 'conflict', 'regression', 'omitted', 'refresh', 'reopen', 'branch-join', 'identical-delete', 'partial-refresh', 'managed-refresh', 'resolve', 'resolve-reopen'])
def test_real_sequential_writers_are_reviewed_and_integrated_from_original_to_final(pipeline_ready, model_server, monkeypatch, mode):  # noqa: F811
    from test_pipeline_dispatch import approve

    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.completions import Completions
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.verification.engine import VerificationEngine

    store, owner, host, workflow, root, author, reviewer, integrator, dispatcher, source, approval = pipeline_ready
    checks = VerificationEngine(dispatcher.root / 'verification').oracles.read(approval).checks
    first_test = 'import runpy\ndef test_parse():\n    assert runpy.run_path("src/parser.py")["value"] >= 1\n'
    if mode in {'branch-join', 'partial-refresh'}:
        (source / 'src/other.py').write_text('value = 0\n')
    if mode == 'identical-delete':
        first_test = 'from pathlib import Path\ndef test_parse():\n    assert not Path("src/parser.py").exists()\n'
        publish = Artifacts.publish
        def publish_deletion(service, scope, identity, fence):
            # The fixture supplies an exact deletion patch; the review still runs real checks.
            (service.workspaces.repository(scope, identity) / 'src/parser.py').unlink()
            return publish(service, scope, identity, fence)
        monkeypatch.setattr(Artifacts, 'publish', publish_deletion)
    if mode == 'regression':
        first_test = first_test.replace('>= 1', '== 1')
    (source / 'tests/test_parser.py').write_text(first_test)
    oracle = source.parent / 'oracle/tests/test_parser.py'
    oracle.write_text(first_test)
    approval = VerificationEngine(dispatcher.root / 'verification').oracles.approve(root.acceptance, checks, oracle.parent.parent)
    if mode == 'resolve-reopen':
        author = store.command(owner, author.id, TaskCommand(operation='revise', expected_revision=author.revision,
            idempotency_key='bound-reopen-resolution-cost', changes={'budget': BudgetAmount(model_calls=1, tool_calls=1).model_dump()}))
    previous = dispatcher.binding(owner, author.id)
    dispatcher.bind(owner, author.id, source, expected_revision=author.revision, expected_binding_id=previous.id,
        key='bind-sequential-first', brain='heuristic_fsm')
    prepared = (store, owner, host, workflow, root, author, reviewer, integrator, dispatcher, source, approval)
    approve(prepared)
    leader = None
    if mode == 'managed-refresh':
        from flycode.orchestration.host import Host

        monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(source.parent / 'host-authority'))
        leader = Host(store.path, source.parent / 'host-state').__enter__()
        store.host = leader
        store._host_database_identity = leader.registration.database_identity
    selected = dispatcher.tick(host, key='sequential-first')
    first_handle = dispatcher.handles[selected.attempt.id]
    try:
        assert first_handle.finished.wait(25), first_handle.error
        assert first_handle.error is None
        checked = dispatcher.tick(host, key='sequential-first-review')
        row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (checked.attempt.id,)).fetchone()
        first_review = Reviews(store, dispatcher.root).get(owner, row['id'])
        assert first_review.status == 'approved'
        first_proof = finish_author(prepared, first_review)
        first_artifact = Artifacts(store, dispatcher.root).get(owner, first_proof.artifact_id)
        second = store.create_task(owner, workflow.id, TaskDraft.model_validate({
            **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Extend the checked repair',
            'dependencies': () if mode in {'conflict', 'omitted', 'branch-join', 'identical-delete', 'partial-refresh', 'resolve', 'resolve-reopen'} else (author.id,),
            'acceptance': root.acceptance.model_copy(update={'id': 'sequential-second-criteria'})}), key='sequential-second')
        second = store.command(owner, second.id, TaskCommand(operation='enqueue', expected_revision=second.revision,
            idempotency_key='enqueue-sequential-second'))
        instance = store.register_instance(host, author.allowed_profile_ids[0])
        attempt = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
            task_id=second.id, expected_task_revision=second.revision, reserve=second.budget,
            idempotency_key='claim-sequential-second')).attempt
        assert attempt is not None
        model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
            'tool': 'edit_file', 'arguments': {'path': 'src/other.py' if mode in {'branch-join', 'partial-refresh'} else 'src/parser.py', 'old_text': 'value = 0' if mode in {'conflict', 'omitted', 'branch-join', 'identical-delete', 'partial-refresh', 'resolve', 'resolve-reopen'} else 'value = 1', 'new_text': 'value = 2'}}]}))
        handle = dispatcher.runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
        try:
            assert handle.finished.wait(25), handle.error
            assert handle.error is None
            second_artifact = Artifacts(store, dispatcher.root).for_attempt(owner, attempt.id)
            expected = first_artifact.baseline if mode in {'conflict', 'omitted', 'branch-join', 'identical-delete', 'partial-refresh', 'resolve', 'resolve-reopen'} else first_artifact.target
            assert second_artifact.baseline.files['src/parser.py'] == expected.files['src/parser.py']
            assert second_artifact.changes[0].before == (first_artifact.changes[0].before if mode in {'conflict', 'omitted', 'branch-join', 'identical-delete', 'partial-refresh', 'resolve', 'resolve-reopen'} else first_artifact.changes[0].after)
            assert (source / 'src/parser.py').read_text() == 'value = 0\n'
            second_oracle = source.parent / 'second-oracle/tests'
            second_oracle.mkdir(parents=True)
            second_test = 'import runpy\ndef test_parse():\n    assert runpy.run_path("src/parser.py")["value"] == 2\n'
            if mode in {'branch-join', 'partial-refresh'}:
                second_test = second_test.replace('src/parser.py', 'src/other.py')
            if mode == 'identical-delete':
                second_test = first_test
            (second_oracle / 'test_parser.py').write_text(second_test)
            second_approval = VerificationEngine(dispatcher.root / 'verification').oracles.approve(
                second.acceptance, checks, second_oracle.parent)
            review_task = store.create_task(owner, workflow.id, TaskDraft(title='Review the sequential change', kind='review',
                objective='Check exact second output and retained first behavior.', parent_id=root.id,
                acceptance=second.acceptance, permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=4),
                allowed_profile_ids=reviewer.allowed_profile_ids, input_artifacts={second_artifact.id: second_artifact.patch_hash}),
                key='sequential-second-review')
            dispatcher.bind_review(owner, review_task.id, second_artifact.id, approval_hash=second_approval,
                expected_revision=review_task.revision, key='bind-sequential-second-review')
            review_task = store.command(owner, review_task.id, TaskCommand(operation='enqueue', expected_revision=review_task.revision,
                idempotency_key='enqueue-sequential-second-review'))
            checker = store.register_instance(host, reviewer.allowed_profile_ids[0])
            review_attempt = store.claim(host, ClaimRequest(instance_id=checker.id, expected_instance_revision=checker.revision,
                task_id=review_task.id, expected_task_revision=review_task.revision, reserve=review_task.budget,
                idempotency_key='claim-sequential-second-review')).attempt
            assert review_attempt is not None
            store.confirm_started(host, review_attempt.id, review_attempt.fence)
            second_review = Reviews(store, dispatcher.root).run(host, review_attempt.id, review_attempt.fence,
                second_artifact.id, approval_hash=second_approval)
            assert second_review.status == 'approved'
            second_proof = Completions(store, dispatcher.root).complete(host, second_artifact.id, second_review.id,
                expected_revision=store.task(owner, second.id).revision, key='complete-sequential-second')
            assert second_proof.prerequisites == attempt.prerequisites
            current = store.task(owner, integrator.id)
            current = store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
                idempotency_key='bind-sequential-inputs', changes={**({'dependencies': (author.id, second.id)} if mode in {'branch-join', 'partial-refresh'} else {}), 'budget': BudgetAmount(tool_calls=40).model_dump(), 'input_artifacts': {
                    item.id: item.patch_hash for item in ((second_artifact,) if mode == 'omitted' else (first_artifact, second_artifact))}}))
            approvals = {item.id: approval for item in (root, author, integrator)} | {second.id: second_approval}
            if mode == 'omitted':
                with pytest.raises(StoreError, match='incomplete_workflow'):
                    dispatcher.bind_integration(owner, current.id, (second_artifact.id,), (second_review.id,),
                        approval_hashes={item.id: approval for item in (root, integrator)} | {second.id: second_approval},
                        expected_revision=current.revision, key='bind-incomplete-author-selection')
                assert (source / 'src/parser.py').read_text() == 'value = 0\n'
                return
            artifact_ids = (second_artifact.id, first_artifact.id) if mode == 'reverse' else (first_artifact.id, second_artifact.id)
            binding = dispatcher.bind_integration(owner, current.id, artifact_ids,
                (first_review.id, second_review.id), approval_hashes=approvals,
                expected_revision=current.revision, key='bind-sequential-integration')
            current = store.command(owner, current.id, TaskCommand(operation='enqueue', expected_revision=current.revision,
                idempotency_key='enqueue-sequential-integration'))
            selected = store.claim(host, ClaimRequest(instance_id=checker.id,
                expected_instance_revision=store.instance(host, checker.id).revision, task_id=current.id,
                expected_task_revision=current.revision, reserve=current.budget, idempotency_key='claim-sequential-integration')).attempt
            assert selected is not None
            if mode in {'branch-join', 'partial-refresh'}:
                workspace = Workspaces(store, dispatcher.root / 'workspaces').prepare(host, selected.id, selected.fence, source)
                joined = Workspaces(store, dispatcher.root / 'workspaces').repository(owner, selected.id)
                assert (joined / 'src/parser.py').read_text() == 'value = 1\n'
                assert (joined / 'src/other.py').read_text() == 'value = 2\n'
                assert set(workspace.prerequisites) == {author.id, second.id}
                assert (source / 'src/other.py').read_text() == 'value = 0\n'
            store.confirm_started(host, selected.id, selected.fence)
            service = Integration(store, dispatcher.root)
            if mode in {'refresh', 'managed-refresh', 'partial-refresh'}:
                from flycode.orchestration.application import Application

                apply = Application._move if mode == 'partial-refresh' else Application.apply
                refreshed = []
                def refresh_after_apply(application, *args, **kwargs):
                    apply(application, *args, **kwargs)
                    if refreshed:
                        return
                    refreshed.append(True)
                    connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
                    try:
                        connection.refresh_results(owner, workflow.id)
                        assert connection.task(owner, author.id).state == 'succeeded'
                        assert connection.task(owner, second.id).state == 'succeeded'
                        assert connection.task(owner, current.id).fence == selected.fence
                    finally:
                        connection.close()
                monkeypatch.setattr(Application, '_move' if mode == 'partial-refresh' else 'apply', refresh_after_apply)
            if mode == 'regression':
                with pytest.raises(StoreError, match='verification_failed'):
                    service.run(host, selected.id, selected.fence,
                        tuple(binding.artifact_hashes), binding.review_ids, approval_hashes=approvals)
                row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (selected.id,)).fetchone()
                assert service.get(owner, row['id']).state == 'failed'
                assert not service.current(owner, row['id'])
                assert (source / 'src/parser.py').read_text() == 'value = 0\n'
                assert store.workflow(owner, workflow.id).state != 'succeeded'
                return
            record = service.run(host, selected.id, selected.fence,
                tuple(binding.artifact_hashes), binding.review_ids, approval_hashes=approvals)
            if mode in {'conflict', 'resolve', 'resolve-reopen'}:
                assert record.state == 'conflict' and record.conflict_paths == ('src/parser.py',)
                assert not service.current(owner, record.id)
                assert (source / 'src/parser.py').read_text() == 'value = 0\n'
                assert store.workflow(owner, workflow.id).state != 'succeeded'
                if mode in {'resolve', 'resolve-reopen'}:
                    resolve_completed_authors(prepared, (first_artifact, second_artifact), record, checker,
                        second.id, second_approval, model_server, reopen=mode == 'resolve-reopen')
                return
            assert record.state == 'completed' and Integration(store, dispatcher.root).current(owner, record.id)
            if mode == 'identical-delete':
                assert not (source / 'src/parser.py').exists()
            else:
                assert (source / 'src/parser.py').read_text() == ('value = 1\n' if mode in {'branch-join', 'partial-refresh'} else 'value = 2\n')
            if mode in {'branch-join', 'partial-refresh'}:
                assert (source / 'src/other.py').read_text() == 'value = 2\n'
            assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
            assert store.workflow(owner, workflow.id).state == 'succeeded'
            net = {change.path: change for change in record.changes}
            assert net['src/parser.py'].before == first_artifact.changes[0].before
            assert net['src/parser.py'].after == (first_artifact.changes[0].after if mode in {'branch-join', 'partial-refresh'} else second_artifact.changes[0].after)
            assert set(record.applied_verdicts) == set(approvals)
            if mode == 'reopen':
                revalidate_sequential_result(prepared, (first_artifact, second_artifact), record, checker,
                    {author.id: approval, second.id: second_approval}, approval)
        finally:
            handle.close()
    finally:
        first_handle.close()
        if leader is not None:
            leader.close()


def test_succeeded_dependency_without_completion_is_a_claim_blocker_not_an_assignment_error(approved_task):  # noqa: F811
    prepared, review = approved_task
    store, owner, host, workflow, _, author, _, _, _, _, _ = prepared
    finish_author(prepared, review)
    with store.transaction():
        current = store.task(owner, author.id)
        store._save_task(changed(current, completion_id=None))
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Missing predecessor proof',
        'dependencies': (author.id,)}), key='missing-proof-child')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-missing-proof-child'))
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    before = store.budget(owner, workflow.id), len(store.attempts(owner, workflow.id))
    result = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=child.id, expected_task_revision=child.revision, reserve=child.budget, idempotency_key='claim-missing-proof'))
    assert result.attempt is None and 'completion_dependency' in result.reasons
    assert (store.budget(owner, workflow.id), len(store.attempts(owner, workflow.id))) == before


@pytest.mark.parametrize('change', ['source', 'evidence'])
def test_predecessor_revocation_after_worker_stop_blocks_dependent_artifact(dependent_ready, model_server, monkeypatch, change):
    prepared, proof, _, attempt = dependent_ready
    store, owner, host, workflow, _, _, _, _, dispatcher, source, _ = prepared
    from flycode.orchestration.review import Reviews

    review = Reviews(store, dispatcher.root).get(owner, proof.review_id)
    publish = Artifacts.publish
    def revoke_then_publish(service, scope, identity, fence):
        if identity == attempt.id:
            if change == 'source':
                (source / 'dirty.bin').write_bytes(b'new owner bytes')
            else:
                (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
        return publish(service, scope, identity, fence)
    monkeypatch.setattr(Artifacts, 'publish', revoke_then_publish)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 1', 'new_text': 'value = 2'}}]}))
    handle = dispatcher.runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.process_stopped.is_set()
        assert store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt.id,)).fetchone()
        assert not store.db.execute('SELECT 1 FROM artifacts WHERE attempt_id=?', (attempt.id,)).fetchone()
        assert any(event.kind == 'artifact_blocked' and event.attempt_id == attempt.id
            for event in store.events(owner, workflow.id))
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    finally:
        handle.close()


@pytest.mark.parametrize('change', ['source', 'evidence', 'baseline', 'original'])
def test_final_checkout_preflight_rejects_changes_before_workspace_publication(dependent_ready, monkeypatch, change):
    from flycode.orchestration import workspaces as module
    from flycode.orchestration.review import Reviews

    prepared, proof, _, attempt = dependent_ready
    store, owner, host, _, _, _, _, _, dispatcher, source, _ = prepared
    review = Reviews(store, dispatcher.root).get(owner, proof.review_id)
    checkout = module._checkout
    def change_after_checkout(repository):
        revision = checkout(repository)
        if change == 'source':
            (source / 'dirty.bin').write_bytes(b'changed after checkout')
        elif change == 'evidence':
            (dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json').unlink()
        else:
            (repository.parent / change / 'src/parser.py').write_text('value = 99\n')
        return revision
    monkeypatch.setattr(module, '_checkout', change_after_checkout)
    workspaces = Workspaces(store, dispatcher.root / 'workspaces')
    before = store.budget(owner, attempt.workflow_id)
    with pytest.raises(StoreError):
        workspaces.prepare(host, attempt.id, attempt.fence, source)
    assert not store.db.execute('SELECT 1 FROM workspaces WHERE attempt_id=?', (attempt.id,)).fetchone()
    assert not store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt.id,)).fetchone()
    assert store.budget(owner, attempt.workflow_id) == before
    assert (source / 'src/parser.py').read_text() == 'value = 0\n'


def revalidate_sequential_result(prepared, artifacts, previous, checker, approvals, root_approval):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, root, _, reviewer, _, dispatcher, source, _ = prepared
    current = store.task(owner, root.id)
    opened = store.command(owner, current.id, TaskCommand(operation='reopen', expected_revision=current.revision,
        idempotency_key='reopen-sequential-result'))
    store.command(owner, opened.id, TaskCommand(operation='enqueue', expected_revision=opened.revision,
        idempotency_key='enqueue-reopened-sequential-result'))
    store.start_next(owner, key='restart-sequential-result')
    fresh_reviews = []
    def claim(task, key):
        return store.claim(host, ClaimRequest(instance_id=checker.id,
            expected_instance_revision=store.instance(host, checker.id).revision, task_id=task.id,
            expected_task_revision=task.revision, reserve=task.budget, idempotency_key=key)).attempt
    for number, artifact in enumerate(artifacts):
        original = store.task(owner, artifact.task_id)
        task = store.create_task(owner, workflow.id, TaskDraft(title=f'Fresh sequential review {number}', kind='review',
            objective='Recheck the retained exact artifact under current owner criteria.', parent_id=root.id,
            acceptance=original.acceptance, permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=4),
            allowed_profile_ids=reviewer.allowed_profile_ids, input_artifacts={artifact.id: artifact.patch_hash}),
            key=f'fresh-sequential-review-{number}')
        dispatcher.bind_review(owner, task.id, artifact.id, approval_hash=approvals[original.id],
            expected_revision=task.revision, key=f'bind-fresh-sequential-review-{number}')
        task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
            idempotency_key=f'enqueue-fresh-sequential-review-{number}'))
        attempt = claim(task, f'claim-fresh-sequential-review-{number}')
        assert attempt is not None
        store.confirm_started(host, attempt.id, attempt.fence)
        review = Reviews(store, dispatcher.root).run(host, attempt.id, attempt.fence, artifact.id,
            approval_hash=approvals[original.id])
        assert review.status == 'approved'
        fresh_reviews.append(review.id)
    task = store.create_task(owner, workflow.id, TaskDraft(title='Revalidate sequential integration', kind='review',
        objective='Verify the installed sequential result without applying its changes twice.', parent_id=root.id,
        acceptance=opened.acceptance, permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=40),
        allowed_profile_ids=reviewer.allowed_profile_ids, input_artifacts={item.id: item.patch_hash for item in artifacts}),
        key='revalidate-sequential-integration')
    approvals = approvals | {root.id: root_approval, task.id: root_approval}
    binding = dispatcher.bind_integration(owner, task.id, tuple(item.id for item in artifacts), tuple(fresh_reviews),
        approval_hashes=approvals, expected_revision=task.revision, key='bind-revalidate-sequential-result',
        previous_result_id=previous.id)
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-revalidate-sequential-result'))
    attempt = claim(task, 'claim-revalidate-sequential-result')
    assert attempt is not None
    store.confirm_started(host, attempt.id, attempt.fence)
    service = Integration(store, dispatcher.root)
    record = service.run(host, attempt.id, attempt.fence, tuple(binding.artifact_hashes), binding.review_ids,
        approval_hashes=approvals, previous_result_id=previous.id)
    assert record.state == 'completed' and service.current(owner, record.id)
    assert record.author_completions == previous.author_completions
    assert record.changes == ()
    assert (source / 'src/parser.py').read_text() == 'value = 2\n'
    assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
    assert not service.current(owner, previous.id)


def test_completed_root_author_resolved_reopen_retains_exact_provenance(owned, multi_writer, model_server, request):  # noqa: F811
    import re

    from test_result_delivery import (
        pending_resolution,
        reviewer,
        test_resolved_root_reopen_requires_fresh_checks_and_preserves_history,
    )

    from flycode.orchestration.completions import Completions
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    store, owner, host, _, root, _, source = owned
    runtime, artifacts, reviews, _, _ = multi_writer
    proof = Completions(store, runtime.root).complete(host, artifacts[0].id, reviews[0].id,
        expected_revision=store.task(owner, root.id).revision, key='complete-root-before-conflict')
    assert proof.task_id == root.id
    runtime, artifacts, conflict, resolver, stopped_integrator, approvals = pending_resolution.__wrapped__(owned, multi_writer, request)
    observed = {}
    def response(message):
        prompt = message['messages'][-1]['content']
        for artifact in artifacts:
            match = re.search(r'\[artifact:' + artifact.id + r' [^\]]+\].*?answer\s*=\s*(\d+)', prompt, re.DOTALL)
            if match:
                observed[artifact.id] = int(match.group(1))
        missing = next((item for item in artifacts if item.id not in observed), None)
        if missing is not None:
            candidate = {'tool': 'read_file', 'arguments': {'artifact_id': missing.id, 'path': 'src/example.py'}}
        elif not re.search(r'(?m)^- read_file\(src/example\.py\):\n', prompt):
            candidate = {'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}
        else:
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/example.py',
                'old_text': 'answer = 0', 'new_text': 'answer = ' + str(sum(observed.values()))}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))
    model_server.respond = response
    handle = runtime.launch(host, resolver.id, resolver.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None and handle.process_stopped.is_set()
    finally:
        handle.close()
    assert observed == {artifacts[0].id: 1, artifacts[1].id: 2}
    patch = Artifacts(store, runtime.root).for_attempt(owner, resolver.id)
    checker = reviewer(owned, [patch], tools=4)
    approval = approvals[root.id]
    review = Reviews(store, runtime.root).run(host, checker.id, checker.fence, patch.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = reviewer(owned, [patch], tools=50)
    required = {root.id, artifacts[1].task_id, stopped_integrator.task_id, resolver.task_id, integrator.task_id}
    service = Integration(store, runtime.root)
    result = service.run(host, integrator.id, integrator.fence, (patch.id,), (review.id,),
        approval_hashes={identity: approval for identity in required})
    assert result.state == 'completed' and service.current(owner, result.id)
    history = [store._attempt(item.attempt_id) for item in artifacts] + [store._attempt(stopped_integrator.id)]
    resolved = runtime, artifacts, conflict, patch, result, approval, history
    test_resolved_root_reopen_requires_fresh_checks_and_preserves_history(owned, resolved)


@pytest.mark.parametrize('saved', ['receipt', 'integration-binding'])
def test_persisted_task1_proof_hashes_survive_additive_lineage_fields(approved_task, saved):  # noqa: F811
    from flycode.domain.results import ReviewExecution
    from flycode.orchestration.completions import Completions
    from flycode.orchestration.integration_bindings import IntegrationBindings

    prepared, review = approved_task
    store, owner, host, workflow, root, author, _, integrator, dispatcher, _, approval = prepared
    artifacts = Artifacts(store, dispatcher.root)
    artifact = artifacts.for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    raw = artifact.model_dump_json(exclude={'patch_hash', 'prerequisites'})
    legacy = artifact.model_dump(mode='json', exclude={'prerequisites'})
    legacy['patch_hash'] = hashlib.sha256(raw.encode()).hexdigest()
    (artifacts.directory / artifact.id / 'manifest.json').write_text(raw)
    store.db.execute('UPDATE artifacts SET data=? WHERE id=?', (json.dumps(legacy), artifact.id))
    checker = store._attempt(review.reviewer_attempt_id)
    checker_task = changed(store.task(owner, checker.task_id), input_artifacts={artifact.id: legacy['patch_hash']})
    with store.transaction():
        store._save_task(checker_task)
        store._save_attempt(changed(checker, contract_hash=checker_task.contract_hash))
    review = changed(review, patch_hash=legacy['patch_hash'], reviewer_contract_hash=checker_task.contract_hash)
    store.db.execute('UPDATE reviews SET data=? WHERE id=?', (review.model_dump_json(), review.id))
    execution = store._load('review_executions', review.reviewer_attempt_id, ReviewExecution)
    execution = changed(execution, patch_hash=legacy['patch_hash'])
    store.db.execute('UPDATE review_executions SET data=? WHERE id=?', (execution.model_dump_json(), execution.attempt_id))
    if saved == 'receipt':
        proof = finish_author(prepared, review)
        # Task1 serialized all of its fields; prerequisites was absent from PatchArtifact.
        proof = changed(proof, artifact_hash=digest(legacy))
        store.db.execute('UPDATE task_completions SET data=? WHERE id=?', (proof.model_dump_json(), proof.id))
        assert Completions(store, dispatcher.root).current(owner, proof.id)
        store.refresh_results(owner, workflow.id)
        assert store.task(owner, author.id).state == 'succeeded'
    else:
        current = store.task(owner, integrator.id)
        current = store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='legacy-integration-input', changes={'input_artifacts': {artifact.id: legacy['patch_hash']}}))
        binding = dispatcher.bind_integration(owner, current.id, (artifact.id,), (review.id,),
            approval_hashes={item.id: approval for item in (root, author, integrator)},
            expected_revision=current.revision, key='legacy-integration-binding')
        binding = changed(binding, artifact_records={artifact.id: digest(legacy)})
        store.db.execute('UPDATE review_bindings SET data=? WHERE id=?',
            (binding.model_dump_json(exclude={'author_completions'}), binding.id))
        restored = IntegrationBindings(store, dispatcher.root).get(host, integrator.id)
        assert IntegrationBindings(store, dispatcher.root).verify_metadata(host, restored) == restored


def test_registered_verification_imports_the_composite_input_and_dependent_artifact(pipeline_ready, model_server):  # noqa: F811
    from flycode.domain.orchestration import (
        AgentProfileDraft,
        BudgetAmount,
        WorkflowBudget,
        WorkflowDraft,
    )
    from flycode.orchestration.projects import Projects
    from flycode.orchestration.review import Reviews
    from flycode.verification.registry import VerificationRegistry

    store, owner, host, _, template, original, reviewer, _, dispatcher, source, approval = pipeline_ready
    project = Projects(store).register(source, actor_id=owner.actor_id)
    owner = owner.model_copy(update={'project_ids': owner.project_ids | {project.id}})
    host = host.model_copy(update={'project_ids': host.project_ids | {project.id}})
    profiles = []
    for old in (original, reviewer):
        profile = store._profile(old.allowed_profile_ids[0])
        profiles.append(store.register_profile(owner, AgentProfileDraft.model_validate({
            **profile.model_dump(include=set(AgentProfileDraft.model_fields)), 'project_ids': (project.id,)})))
        store.register_instance(host, profiles[-1].id)
    draft = TaskDraft.model_validate(template.model_dump(include=set(TaskDraft.model_fields)))
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=project.id, task=draft, budget=WorkflowBudget()),
        key='registered-dependency-workflow')
    root = store.task(owner, workflow.root_task_id)
    author = store.create_task(owner, workflow.id, draft.model_copy(update={
        'parent_id': root.id, 'allowed_profile_ids': (profiles[0].id,)}), key='registered-first-author')
    dispatcher.bind(owner, author.id, source, expected_revision=author.revision,
        key='bind-registered-first', brain='heuristic_fsm')
    for task in (root, author):
        store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
            idempotency_key='enqueue-' + task.id))
    store.start_next(owner, key='start-registered-dependency-workflow')
    selected = dispatcher.tick(host, key='run-registered-first')
    assert selected.attempt is not None
    first_handle = dispatcher.handles[selected.attempt.id]
    assert first_handle.finished.wait(25), first_handle.error
    assert first_handle.error is None
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, selected.attempt.id)
    checker = store.create_task(owner, workflow.id, TaskDraft(title='Check registered first output', kind='review',
        objective='Verify exact registered author bytes.', parent_id=root.id, acceptance=author.acceptance,
        permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=4), allowed_profile_ids=(profiles[1].id,),
        input_artifacts={artifact.id: artifact.patch_hash}), key='registered-first-review')
    dispatcher.bind_review(owner, checker.id, artifact.id, approval_hash=approval,
        expected_revision=checker.revision, key='bind-registered-first-review')
    store.command(owner, checker.id, TaskCommand(operation='enqueue', expected_revision=checker.revision,
        idempotency_key='enqueue-registered-first-review'))
    checked = dispatcher.tick(host, key='run-registered-first-review')
    assert checked.attempt is not None and checked.attempt.task_id == checker.id
    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (checked.attempt.id,)).fetchone()
    review = Reviews(store, dispatcher.root).get(owner, row['id'])
    from flycode.orchestration.completions import Completions

    Completions(store, dispatcher.root).complete(host, artifact.id, review.id,
        expected_revision=store.task(owner, author.id).revision, key='complete-registered-first')
    first_handle.close()
    child = store.create_task(owner, workflow.id, TaskDraft.model_validate({
        **author.model_dump(include=set(TaskDraft.model_fields)), 'title': 'Registered dependent input',
        'parent_id': root.id, 'dependencies': (author.id,)}), key='registered-dependent-input')
    binding = dispatcher.bind(owner, child.id, source, expected_revision=child.revision,
        key='bind-registered-dependent', brain='heuristic_fsm')
    child = store.command(owner, child.id, TaskCommand(operation='enqueue', expected_revision=child.revision,
        idempotency_key='enqueue-registered-dependent'))
    attempt = store.dispatch_bound(host, runtime_root=str(dispatcher.root), binding_ids=(binding.id,),
        key='dispatch-registered-dependent').attempt
    assert attempt is not None and attempt.task_id == child.id
    workspaces = Workspaces(store, dispatcher.root / 'workspaces')
    workspace = workspaces.prepare(host, attempt.id, attempt.fence, source)
    registry = VerificationRegistry(store)
    baseline = registry.register_workspace_subject(owner, attempt.id)
    assert baseline.project_id == project.id
    assert baseline.snapshot.files == workspace.input_snapshot.files
    assert baseline.snapshot.ref.attempt_id == attempt.id
    assert baseline.origin.source_subject_hash == workspace.snapshot.subject_hash
    assert (registry.subject_root(owner, baseline.id) / 'src/parser.py').read_text() == 'value = 1\n'
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 1', 'new_text': 'value = 2'}}]}))
    handle = dispatcher.runtime.launch(host, attempt.id, attempt.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None
        artifact = Artifacts(store, dispatcher.root).for_attempt(owner, attempt.id)
        for stage, expected in (('baseline', 1), ('target', 2)):
            subject = registry.register_artifact_subject(owner, artifact.id, stage=stage)
            assert (registry.subject_root(owner, subject.id) / 'src/parser.py').read_text() == f'value = {expected}\n'
            assert subject.snapshot.ref.attempt_id == attempt.id
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
    finally:
        handle.close()


def test_owner_edit_during_completion_evidence_preflight_cannot_publish_receipt(approved_task, monkeypatch):  # noqa: F811
    from flycode.orchestration.completions import Completions

    prepared, review = approved_task
    store, owner, host, _, _, author, _, _, dispatcher, source, _ = prepared
    service = Completions(store, dispatcher.root)
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    check = service._review_evidence
    def edit_after_check(*args, **kwargs):
        check(*args, **kwargs)
        (source / 'dirty.bin').write_bytes(b'owner changed bytes during completion')
    monkeypatch.setattr(service, '_review_evidence', edit_after_check)
    with pytest.raises(StoreError):
        service.complete(host, artifact.id, review.id, expected_revision=store.task(owner, author.id).revision,
            key='completion-source-content-race')
    assert store.task(owner, author.id).state == 'awaiting_review'
    assert not store.db.execute('SELECT 1 FROM task_completions').fetchone()
    assert (source / 'dirty.bin').read_bytes() == b'owner changed bytes during completion'


def resolve_completed_authors(prepared, artifacts, conflict, checker, second_id, second_approval, model_server, *, reopen=False):
    import re

    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.completions import Completions
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, root, author, reviewer, _, dispatcher, source, approval = prepared
    identities = {item.task_id: store.task(owner, item.task_id).completion_id for item in artifacts}
    task = store.task(owner, conflict.resolution_task_id)
    task = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
        idempotency_key='approve-completed-author-resolution', changes={
            'objective': 'Read both exact pinned versions and the workspace. Keep the larger checked value.',
            'acceptance': root.acceptance.model_dump(), 'permissions': root.permissions.model_dump(),
            'budget': BudgetAmount(model_calls=4 if reopen else 8, tool_calls=4 if reopen else 12).model_dump()}))
    task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
        idempotency_key='enqueue-completed-author-resolution'))
    instance = store.register_instance(host, author.allowed_profile_ids[0])
    resolver = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=task.budget,
        idempotency_key='claim-completed-author-resolution')).attempt
    assert resolver is not None
    observed = {}
    def response(request):
        prompt = request['messages'][-1]['content']
        for artifact in artifacts:
            match = re.search(r'\[artifact:' + artifact.id + r' [^\]]+\].*?value\s*=\s*(\d+)', prompt, re.DOTALL)
            if match:
                observed[artifact.id] = int(match.group(1))
        missing = next((item for item in artifacts if item.id not in observed), None)
        if missing is not None:
            candidate = {'tool': 'read_file', 'arguments': {'artifact_id': missing.id, 'path': 'src/parser.py'}}
        elif not re.search(r'(?m)^- read_file\(src/parser\.py\):\n', prompt):
            candidate = {'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}
        else:
            candidate = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py',
                'old_text': 'value = 0', 'new_text': 'value = ' + str(max(observed.values()))}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))
    model_server.respond = response
    handle = dispatcher.runtime.launch(host, resolver.id, resolver.fence, source, brain='heuristic_fsm')
    try:
        assert handle.finished.wait(25), handle.error
        assert handle.error is None
        assert observed == {artifacts[0].id: 1, artifacts[1].id: 2}
        patch = Artifacts(store, dispatcher.root).for_attempt(owner, resolver.id)
        reads = [call for call in Tools(store).calls(owner, workflow.id)
            if call.attempt_id == resolver.id and call.execution_profile == 'artifact-read-v1']
        assert len(reads) == 2 and all(call.status == 'completed' for call in reads)
        assert all(store._attempt(item.attempt_id).state == 'succeeded' for item in artifacts)
        def claim(stage, key):
            attempt = store.claim(host, ClaimRequest(instance_id=checker.id,
                expected_instance_revision=store.instance(host, checker.id).revision, task_id=stage.id,
                expected_task_revision=stage.revision, reserve=stage.budget, idempotency_key=key)).attempt
            assert attempt is not None
            store.confirm_started(host, attempt.id, attempt.fence)
            return attempt
        def stage(title, tools):
            item = store.create_task(owner, workflow.id, TaskDraft(title=title, kind='review', objective='Check the resolved exact result.',
                parent_id=root.id, acceptance=root.acceptance, permissions=reviewer.permissions,
                budget=BudgetAmount(tool_calls=tools), allowed_profile_ids=reviewer.allowed_profile_ids,
                input_artifacts={patch.id: patch.patch_hash}), key=title)
            return store.command(owner, item.id, TaskCommand(operation='enqueue', expected_revision=item.revision,
                idempotency_key='enqueue-' + title))
        check = stage('Review completed-author resolution', 4)
        attempt = claim(check, 'claim-completed-author-resolution-review')
        review = Reviews(store, dispatcher.root).run(host, attempt.id, attempt.fence, patch.id, approval_hash=approval)
        assert review.status == 'approved'
        integrator = stage('Integrate completed-author resolution', 55)
        required = {root.id, author.id, second_id, task.id, integrator.id, store._attempt(conflict.attempt_id).task_id}
        approvals = {identity: second_approval if identity == second_id else approval for identity in required}
        attempt = claim(integrator, 'claim-completed-author-resolution-integration')
        service = Integration(store, dispatcher.root)
        result = service.run(host, attempt.id, attempt.fence, (patch.id,), (review.id,), approval_hashes=approvals)
        assert result.state == 'completed' and service.current(owner, result.id)
        assert set(result.applied_verdicts) == required
        assert result.resolved_conflicts == (conflict.id,)
        assert store.workflow(owner, workflow.id).state == 'succeeded'
        assert (source / 'src/parser.py').read_text() == 'value = 2\n'
        assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'
        for identity, proof in identities.items():
            assert store.task(owner, identity).completion_id == proof
            assert Completions(store, dispatcher.root).current(owner, proof)
        if reopen:
            revalidate_resolved_receipts(prepared, patch, result, checker, approvals)
    finally:
        handle.close()


@pytest.mark.parametrize('operation', ['complete', 'current'])
def test_final_source_read_cannot_hide_a_replaced_physical_owner_root(approved_task, monkeypatch, operation):  # noqa: F811
    from flycode.orchestration import completions

    prepared, review = approved_task
    store, owner, host, _, _, author, _, _, dispatcher, source, _ = prepared
    proof = finish_author(prepared, review) if operation == 'current' else None
    service = completions.Completions(store, dispatcher.root)
    artifact = Artifacts(store, dispatcher.root).get(owner, proof.artifact_id) if proof else Artifacts(
        store, dispatcher.root).for_attempt(owner, store.task(owner, author.id).current_attempt_id)
    capture = completions.capture
    reads = []
    def replace_after_final_read(path, *args, **kwargs):
        result = capture(path, *args, **kwargs)
        if path == source:
            reads.append(True)
            if len(reads) == 2:
                retained = source.with_name('retained-completion-source')
                source.rename(retained)
                shutil.copytree(retained, source)
        return result
    monkeypatch.setattr(completions, 'capture', replace_after_final_read)
    if operation == 'current':
        assert not service.current(owner, proof.id)
    else:
        with pytest.raises(StoreError):
            service.complete(host, artifact.id, review.id, expected_revision=store.task(owner, author.id).revision,
                key='complete-replaced-physical-source')
        assert not store.db.execute('SELECT 1 FROM task_completions').fetchone()
    assert len(reads) == 2
    assert (source / 'dirty.bin').read_bytes() == b'\x00\xff keep owner bytes\r\n'


def revalidate_resolved_receipts(prepared, patch, previous, checker, previous_approvals):
    from flycode.domain.orchestration import BudgetAmount
    from flycode.orchestration.completions import Completions
    from flycode.orchestration.integration import Integration
    from flycode.orchestration.review import Reviews

    store, owner, host, workflow, root, _, reviewer, _, dispatcher, source, approval = prepared
    current = store.task(owner, root.id)
    current = store.command(owner, current.id, TaskCommand(operation='reopen', expected_revision=current.revision,
        idempotency_key='reopen-resolved-receipts'))
    store.command(owner, current.id, TaskCommand(operation='enqueue', expected_revision=current.revision,
        idempotency_key='enqueue-reopened-resolved-receipts'))
    store.start_next(owner, key='restart-resolved-receipts')
    def stage(title, tools):
        return store.create_task(owner, workflow.id, TaskDraft(title=title, kind='review',
            objective='Reverify the installed resolution and every retained owner criterion.', parent_id=patch.task_id,
            acceptance=root.acceptance, permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=tools),
            allowed_profile_ids=reviewer.allowed_profile_ids, input_artifacts={patch.id: patch.patch_hash}), key=title)
    def claim(task, key):
        task = store.command(owner, task.id, TaskCommand(operation='enqueue', expected_revision=task.revision,
            idempotency_key='enqueue-' + key))
        attempt = store.claim(host, ClaimRequest(instance_id=checker.id,
            expected_instance_revision=store.instance(host, checker.id).revision, task_id=task.id,
            expected_task_revision=task.revision, reserve=task.budget, idempotency_key=key)).attempt
        assert attempt is not None
        store.confirm_started(host, attempt.id, attempt.fence)
        return attempt
    check = stage('Fresh review of resolved receipts', 4)
    dispatcher.bind_review(owner, check.id, patch.id, approval_hash=approval,
        expected_revision=check.revision, key='bind-fresh-resolved-receipts-review')
    attempt = claim(check, 'claim-fresh-resolved-receipts-review')
    review = Reviews(store, dispatcher.root).run(host, attempt.id, attempt.fence, patch.id, approval_hash=approval)
    assert review.status == 'approved'
    integrator = stage('Revalidate resolved receipts', 48)
    approvals = {identity: value for identity, value in previous_approvals.items()
        if identity != store._attempt(previous.attempt_id).task_id} | {integrator.id: approval}
    binding = dispatcher.bind_integration(owner, integrator.id, (patch.id,), (review.id,), approval_hashes=approvals,
        expected_revision=integrator.revision, key='bind-revalidate-resolved-receipts', previous_result_id=previous.id)
    service = Integration(store, dispatcher.root)
    conflict = service.get(owner, previous.resolved_conflicts[0])
    completions = Completions(store, dispatcher.root)
    assert completions.require_bound(owner, conflict, context=binding, coverage=False) == set(conflict.author_completions)
    for reference in conflict.author_completions.values():
        with pytest.raises(StoreError):
            completions.metadata(owner, reference.id)
    task_id, reference = next(iter(conflict.author_completions.items()))
    forged = conflict.model_copy(update={'author_completions': {
        **conflict.author_completions, task_id: reference.model_copy(update={'sha256': '0' * 64})}})
    with pytest.raises(StoreError):
        completions.require_bound(owner, forged, context=binding, coverage=False)
    with pytest.raises(StoreError):
        completions.require_bound(owner, conflict, context=binding.model_copy(update={'resolved_conflicts': ()}), coverage=False)
    author = store._attempt(conflict.author_attempts[task_id])
    for changes in ({'contract_hash': '0' * 64}, {'semantic_revision': author.semantic_revision + 1},
            {'task_id': patch.task_id}, {'workflow_id': 'f' * 32}, {'fence': author.fence + 1}):
        with store.transaction():
            store._save_attempt(changed(author, **changes))
            try:
                with pytest.raises(StoreError):
                    completions.require_bound(owner, conflict, context=binding, coverage=False)
            finally:
                store._save_attempt(author)
    attempt = claim(integrator, 'claim-revalidate-resolved-receipts')
    record = service.run(host, attempt.id, attempt.fence, tuple(binding.artifact_hashes), binding.review_ids,
        approval_hashes=approvals, previous_result_id=previous.id)
    assert record.state == 'completed' and service.current(owner, record.id)
    assert record.changes == () and record.resolved_conflicts == previous.resolved_conflicts
    assert store.budget(owner, workflow.id).spent.tool_calls <= 120
    assert (source / 'src/parser.py').read_text() == 'value = 2\n'
