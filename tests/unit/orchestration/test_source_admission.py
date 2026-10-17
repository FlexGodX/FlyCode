"""A source claim outlives a coordinator until every owned effect is settled."""
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_result_delivery import delivery  # noqa: F401

from flycode.orchestration.store import Store, StoreError
from flycode.verification.custody import locked


def service(store):
    assert importlib.util.find_spec('flycode.orchestration.sources') is not None, 'Shared source admission is missing'
    from flycode.orchestration.sources import Sources
    return Sources(store)


def identity(descriptor):
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def acquire(sources, root, coordinator, descriptor, owner='legacy-one'):
    return sources.acquire(sources.prepare(root), owner_kind='legacy', owner_id=owner,
        coordinator_path=str(coordinator), coordinator_identity=identity(descriptor))


@pytest.fixture
def source_store(tmp_path):
    store = Store(tmp_path / 'source.sqlite')
    source = tmp_path / 'source'
    (source / 'child').mkdir(parents=True)
    (source / 'keep.bin').write_bytes(b'\x00\xff owner bytes\r\n')
    try:
        yield store, source
    finally:
        store.close()


@pytest.mark.parametrize('alias', ['exact', 'child', 'parent', 'symlink'])
def test_claim_blocks_overlapping_root_aliases_and_retains_owner_bytes(source_store, tmp_path, alias):
    store, root = source_store
    sources = service(store)
    first = root / 'child' if alias == 'parent' else root
    second = root / 'child' if alias == 'child' else root
    if alias == 'symlink':
        second = tmp_path / 'alias'
        second.symlink_to(root, target_is_directory=True)
    coordinator = tmp_path / 'coordinator.lock'
    other = tmp_path / 'other.lock'
    with locked(coordinator, create=True) as descriptor, locked(other, create=True) as competing:
        claim = acquire(sources, first, coordinator, descriptor)
        with pytest.raises(StoreError, match='source_busy'):
            acquire(sources, second, other, competing, owner='legacy-two')
        with pytest.raises(StoreError, match='coordinator_running'):
            sources.settle(claim.id)
        assert (root / 'keep.bin').read_bytes() == b'\x00\xff owner bytes\r\n'
    assert sources.settle(claim.id).state == 'released'
    assert sources.settle(claim.id).state == 'released'


def test_two_connections_cannot_admit_overlapping_sources(source_store, tmp_path):
    store, root = source_store
    service(store)
    barrier = Barrier(2)
    def claim(number):
        connection = Store(store.path)
        coordinator = tmp_path / f'coordinator-{number}.lock'
        try:
            sources = service(connection)
            with locked(coordinator, create=True) as descriptor:
                barrier.wait(timeout=5)
                try:
                    return acquire(sources, root, coordinator, descriptor, owner=f'legacy-{number}').id
                except StoreError as error:
                    return error.code
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, range(2)))
    assert outcomes.count('source_busy') == 1
    assert len([value for value in outcomes if len(value) == 32]) == 1


def test_unknown_effects_and_unconfirmed_process_remain_held_after_restart(source_store, tmp_path):
    from flycode.domain.verification import ProcessRef

    store, root = source_store
    sources = service(store)
    coordinator = tmp_path / 'coordinator.lock'
    with locked(coordinator, create=True) as descriptor:
        claim = acquire(sources, root, coordinator, descriptor)
        ref = ProcessRef(id='a' * 32, directory=str(tmp_path / 'missing-process'), device=1, inode=1, request_hash='b' * 64)
        sources.bind_process(claim.id, ref)
    reopened = Store(store.path)
    try:
        with pytest.raises(StoreError, match='process_unconfirmed'):
            service(reopened).settle(claim.id)
        assert sources.get(claim.id).state == 'held'
    finally:
        reopened.close()
    sources.mark_uncertain(claim.id, 'External effect requires trusted reconciliation')
    with pytest.raises(StoreError, match='effects_unknown'):
        sources.settle(claim.id)


def test_disjoint_siblings_are_admitted_and_stale_owner_cannot_guard(source_store, tmp_path):
    store, root = source_store
    sources = service(store)
    sibling = tmp_path / 'source-other'
    sibling.mkdir()
    coordinator = tmp_path / 'first.lock'
    other = tmp_path / 'second.lock'
    with locked(coordinator, create=True) as descriptor, locked(other, create=True) as competing:
        claim = acquire(sources, root, coordinator, descriptor)
        second = acquire(sources, sibling, other, competing, owner='legacy-two')
        with pytest.raises(StoreError, match='source_owner'):
            sources.guard(claim.id, 'legacy-two')
    sources.settle(claim.id)
    with pytest.raises(StoreError, match='source_released'):
        sources.guard(claim.id, 'legacy-one')
    sources.settle(second.id)


def test_live_legacy_source_claim_blocks_actual_integration_before_checks(owned, delivery, tmp_path):  # noqa: F811
    from test_result_delivery import reviewer

    from flycode.orchestration.integration import Integration

    store, owner, host, workflow, task, _, root = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    sources = service(store)
    coordinator = tmp_path / 'legacy-running.lock'
    before = store.budget(owner, workflow.id)
    with locked(coordinator, create=True) as descriptor:
        claim = acquire(sources, root, coordinator, descriptor)
        with pytest.raises(StoreError, match='source_busy'):
            Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
        assert store.budget(owner, workflow.id) == before
        assert (root / 'src/example.py').read_text() == 'answer = 0\n'
        assert (root / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    sources.settle(claim.id)


def test_external_flag_is_durable_without_revoking_active_owner(source_store, tmp_path):
    store, root = source_store
    sources = service(store)
    coordinator = tmp_path / 'coordinator.lock'
    with locked(coordinator, create=True) as descriptor:
        claim = acquire(sources, root, coordinator, descriptor)
        sources.note_external(claim.id)
        assert sources.guard(claim.id, claim.owner_id).external_effects
    with pytest.raises(StoreError, match='effects_unknown'):
        sources.settle(claim.id)


def test_actual_integration_claim_blocks_legacy_and_releases_only_stopped_receipts(owned, delivery, tmp_path, monkeypatch):  # noqa: F811
    from test_result_delivery import reviewer

    from flycode.orchestration.integration import Integration
    from flycode.verification.custody import stopped
    from flycode.web.runs import RunManager, RunRequest

    store, owner, host, _workflow, task, _, root = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    integration = Integration(store, runtime.root)
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    manager = RunManager(root.parent, tmp_path / 'legacy-history', tmp_path / 'empty-catalogue')
    sources = service(store)
    verify = integration._verify
    seen = []
    def verify_while_owned(scope, record, *args, **kwargs):
        assert record.source_claim_id
        seen.append(record.source_claim_id)
        coordinator = tmp_path / ('competing-' + str(len(seen)) + '.lock')
        with locked(coordinator, create=True) as descriptor, pytest.raises(StoreError, match='source_busy'):
            acquire(sources, root, coordinator, descriptor)
        with pytest.raises(RuntimeError, match='source_busy'):
            manager.start(RunRequest(task='Inspect the source.', project=root.name, model='reasoner', decision_model=None))
        assert not manager.history()
        return verify(scope, record, *args, **kwargs)
    monkeypatch.setattr(integration, '_verify', verify_while_owned)
    result = integration.run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
        approval_hashes={task.id: approval, integrator.task_id: approval})
    assert result.state == 'completed' and integration.current(owner, result.id)
    claim = sources.get(result.source_claim_id)
    assert seen and claim.state == 'released' and claim.processes
    for ref in claim.processes:
        with stopped(ref) as receipt:
            assert receipt.started and receipt.stop_confirmed
    assert (root / 'src/example.py').read_text() == 'answer = 1\n'
    assert (root / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_real_legacy_http_run_blocks_integration_and_retains_late_usage(owned, delivery, tmp_path, model_server, monkeypatch):  # noqa: F811
    import json
    import time
    from threading import Event

    from test_result_delivery import reviewer

    from flycode.orchestration.integration import Integration
    from flycode.web.runs import ACTIVE, RunManager, RunRequest

    store, owner, host, workflow, task, _, root = owned
    runtime, artifact, review, approval = delivery
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    entered, release = Event(), Event()
    def respond(request):
        entered.set()
        assert release.wait(10)
        return model_server.completion(json.dumps({'candidates': [
            {'tool': 'create_file', 'arguments': {'path': 'late.txt', 'content': 'late'}}]}))
    model_server.respond = respond
    manager = RunManager(root.parent, tmp_path / 'legacy-history', tmp_path / 'empty-catalogue')
    record = manager.start(RunRequest(task='Repair without changing user files.', project=root.name,
        model='reasoner', decision_model=None, max_steps=5))
    try:
        assert entered.wait(8), manager.snapshot(record['id'])
        integrator = reviewer(owned, [artifact])
        before = store.budget(owner, workflow.id)
        with pytest.raises(StoreError, match='source_busy'):
            Integration(store, runtime.root).run(host, integrator.id, integrator.fence, (artifact.id,), (review.id,),
                approval_hashes={task.id: approval, integrator.task_id: approval})
        assert store.budget(owner, workflow.id) == before
        assert not store.db.execute('SELECT 1 FROM integrations').fetchone()
        assert (root / 'src/example.py').read_text() == 'answer = 0\n'
    finally:
        manager.stop(record['id'])
        release.set()
        deadline = time.monotonic() + 12
        while manager.snapshot(record['id'])['status'] in ACTIVE and time.monotonic() < deadline:
            time.sleep(.02)
        manager.close()
    stopped = manager.snapshot(record['id'])
    assert stopped['status'] == 'stopped', stopped
    assert service(store).get(stopped['source_claim_id']).state == 'released'
    calls = [json.loads(row['data']) for row in store.db.execute('SELECT data FROM model_calls WHERE attempt_id IS NULL')]
    assert len(calls) == 1 and calls[0]['owner_id'] == record['id']
    assert calls[0]['status'] == 'completed' and calls[0]['late'] and calls[0]['usage']['total_tokens'] == 11
    assert not (root / 'late.txt').exists()
    assert (root / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_unclaimed_old_integration_blocks_new_source_writer(owned, delivery, tmp_path):  # noqa: F811
    from test_result_delivery import reviewer

    from flycode.domain.results import IntegrationRecord
    from flycode.verification.snapshot import capture

    store, owner, host, workflow, task, _, root = owned
    runtime, artifact, review, approval = delivery
    integrator = reviewer(owned, [artifact])
    tasks = store.tasks(owner, workflow.id)
    record = IntegrationRecord(id='c' * 32, project_id=task.project_id, workflow_id=workflow.id,
        attempt_id=integrator.id, fence=integrator.fence, source_root=str(root), runtime_root=str(runtime.root),
        source_before=capture(root), artifact_hashes={artifact.id: artifact.patch_hash}, review_ids=(review.id,),
        contracts={item.id: item.contract_hash for item in tasks}, semantic_revisions={item.id: item.semantic_revision for item in tasks},
        author_attempts={task.id: artifact.attempt_id}, approval_hashes={task.id: approval, integrator.task_id: approval}, created_at=store.clock())
    with store.transaction():
        store._claim_execution(host, integrator.id, integrator.fence, 'integration')
        store.db.execute('INSERT INTO integrations VALUES (?,?,?,?,?)',
            (record.id, workflow.id, integrator.id, record.state, record.model_dump_json()))
    sources = service(store)
    coordinator = tmp_path / 'new-legacy.lock'
    with locked(coordinator, create=True) as descriptor, pytest.raises(StoreError, match='source_busy'):
        acquire(sources, root, coordinator, descriptor)


def test_legacy_http_owner_must_settle_before_source_is_released(source_store, tmp_path):
    from flycode.domain.orchestration import HostModelCall

    store, root = source_store
    sources = service(store)
    coordinator = tmp_path / 'coordinator.lock'
    with locked(coordinator, create=True) as descriptor:
        claim = acquire(sources, root, coordinator, descriptor)
    call = HostModelCall(id='a' * 32, owner_id=claim.owner_id, owner_kind='legacy', endpoint_ref='endpoint',
        model_id='reasoner', role='primary', started_at=store.clock())
    store.db.execute('INSERT INTO model_calls VALUES (?,?,?,?,?)', (call.id, None, call.endpoint_ref, call.status, call.model_dump_json()))
    with pytest.raises(StoreError, match='resource_busy'):
        sources.settle(claim.id)
    finished = call.model_copy(update={'status': 'completed'})
    store.db.execute("UPDATE model_calls SET state='completed',data=? WHERE id=?", (finished.model_dump_json(), call.id))
    assert sources.settle(claim.id).state == 'released'


def test_owned_tool_process_is_bound_before_spawn_and_required_at_settlement(source_store, tmp_path):
    import sys

    from flycode.verification.custody import run

    store, root = source_store
    sources = service(store)
    coordinator = tmp_path / 'coordinator.lock'
    output = tmp_path / 'tool'
    output.mkdir()
    marker = root / 'owned.txt'
    with locked(coordinator, create=True) as descriptor:
        claim = acquire(sources, root, coordinator, descriptor)
        def prepare(ref):
            assert not marker.exists()
            sources.bind_process(claim.id, ref)
        with (output / 'stdout.txt').open('wb') as stdout, (output / 'stderr.txt').open('wb') as stderr:
            receipt = run([sys.executable, '-c', 'from pathlib import Path; Path("owned.txt").write_text("actual tool")'],
                root, dict(os.environ), stdout, stderr, output, 10, lambda: False, lambda state: None, lambda ref: None,
                version='tool-custody-v1', on_prepare=prepare)
        assert receipt.started and receipt.stop_confirmed and marker.read_text() == 'actual tool'
        assert sources.get(claim.id).processes == (receipt.ref,)
    (output / 'process.lock').unlink()
    with pytest.raises(StoreError, match='process_unconfirmed'):
        sources.settle(claim.id)
    assert sources.get(claim.id).state == 'held'


def test_schema15_failure_does_not_destroy_schema14_history(source_store, monkeypatch):
    import sqlite3

    store, _ = source_store
    store.db.execute('DROP TABLE source_claims')
    store.db.execute('PRAGMA user_version=14')
    before = [tuple(row) for row in store.db.execute('SELECT * FROM commands')]
    connect = sqlite3.connect
    def interrupted(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '15' else sqlite3.SQLITE_OK)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', interrupted)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        Store(store.path)
    probe = connect(store.path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 14
        assert probe.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='source_claims'").fetchone()[0] == 0
    finally:
        probe.close()
    assert [tuple(row) for row in store.db.execute('SELECT * FROM commands')] == before
