"""Unknown legacy writers veto every source rollback, including crash recovery."""
import os
import shutil

import pytest
from test_atomic_application import installation  # noqa: F401
from test_result_delivery import delivery, reviewer  # noqa: F401

from flycode.orchestration.integration import Integration
from flycode.orchestration.sources import Sources
from flycode.orchestration.store import StoreError, changed
from flycode.verification.custody import locked


class InjectedCrash(BaseException):
    pass


def quarantine(store, root, coordinator):
    sources = Sources(store)
    with locked(coordinator, create=True) as descriptor:
        info = os.fstat(descriptor)
        return sources.acquire(sources.prepare(root), owner_kind='legacy', owner_id=coordinator.name,
            coordinator_path=coordinator, coordinator_identity=(info.st_dev, info.st_ino), quarantine=True)


def applied_then_failed(owned, prepared, tmp_path, monkeypatch, *, crash):
    store, _, host, _, task, _, root = owned
    runtime, artifact, review, approval = prepared
    attempt = reviewer(owned, [artifact])
    service = Integration(store, runtime.root)
    original = service._verify
    def fail_applied(scope, record, *args, **kwargs):
        if kwargs['applied']:
            assert (root / 'src/example.py').read_text() == 'answer = 1\n'
            quarantine(store, root, tmp_path / 'unknown-legacy.lock')
            if crash:
                raise InjectedCrash()
            raise StoreError('injected_after_apply')
        return original(scope, record, *args, **kwargs)
    monkeypatch.setattr(service, '_verify', fail_applied)
    with pytest.raises(InjectedCrash if crash else StoreError):
        service.run(host, attempt.id, attempt.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, attempt.task_id: approval})
    record = service.get(host, store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (attempt.id,)).fetchone()['id'])
    return service, record, attempt


def test_quarantine_after_apply_blocks_normal_failure_rollback(owned, delivery, tmp_path, monkeypatch):  # noqa: F811
    store, _, host, _, _, _, root = owned
    service, record, attempt = applied_then_failed(owned, delivery, tmp_path, monkeypatch, crash=False)
    assert (root / 'src/example.py').read_text() == 'answer = 1\n', 'Rollback wrote over an unknown legacy writer'
    assert record.state == 'reconciling' and not store._attempt(attempt.id).stop_confirmed
    assert Sources(store).get(record.source_claim_id).state == 'held'
    assert service.reconcile(host, record.id).state == 'reconciling'
    assert (root / 'src/example.py').read_text() == 'answer = 1\n'


@pytest.mark.parametrize('old_record', [False, True])
def test_quarantine_blocks_recovery_rollback_even_without_historical_claim(owned, delivery, tmp_path, monkeypatch, old_record):  # noqa: F811
    store, _, host, _, _, _, root = owned
    service, record, attempt = applied_then_failed(owned, delivery, tmp_path, monkeypatch, crash=True)
    assert (root / 'src/example.py').read_text() == 'answer = 1\n'
    if old_record:
        with store.transaction():
            record = changed(record, source_claim_id=None)
            store.db.execute('UPDATE integrations SET data=? WHERE id=?', (record.model_dump_json(), record.id))
    result = service.reconcile(host, record.id)
    assert (root / 'src/example.py').read_text() == 'answer = 1\n', 'Recovery wrote while source ownership was unknown'
    assert result.state == 'reconciling' and not store._attempt(attempt.id).stop_confirmed
    assert (root / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'


def test_quarantine_at_last_comparison_blocks_first_rollback_rename(installation, tmp_path, monkeypatch):  # noqa: F811
    from flycode.orchestration import application
    from flycode.orchestration.store import Store

    installer, change, target = installation
    installer.apply((change,), lambda: None)
    store = Store(tmp_path / 'source.sqlite')
    coordinator = tmp_path / 'owner.lock'
    try:
        with locked(coordinator, create=True) as descriptor:
            info = os.fstat(descriptor)
            sources = Sources(store)
            claim = sources.acquire(sources.prepare(installer.source), owner_kind='legacy', owner_id='current-owner',
                coordinator_path=coordinator, coordinator_identity=(info.st_dev, info.st_ino))
            version = application.version
            inserted = []
            def compare_then_quarantine(path):
                result = version(path)
                if path == target and not inserted:
                    inserted.append(quarantine(store, installer.source, tmp_path / 'late-quarantine.lock'))
                return result
            monkeypatch.setattr(application, 'version', compare_then_quarantine)
            assert not installer.rollback((change,), lambda: sources.guard(claim.id, claim.owner_id))
            assert target.read_bytes() == b'after\n'
            assert (installer.recovery / '0').read_bytes() == b'before\n'
            assert inserted and sources.get(claim.id).state == 'held'
    finally:
        store.close()


def test_quarantine_after_exchange_blocks_restorative_rollback_rename(installation, tmp_path, monkeypatch):  # noqa: F811
    from flycode.orchestration.store import Store

    installer, change, target = installation
    installer.apply((change,), lambda: None)
    store = Store(tmp_path / 'source.sqlite')
    coordinator = tmp_path / 'owner.lock'
    moves = []
    try:
        with locked(coordinator, create=True) as descriptor, target.open('r+b') as installed:
            info = os.fstat(descriptor)
            sources = Sources(store)
            claim = sources.acquire(sources.prepare(installer.source), owner_kind='legacy', owner_id='current-owner',
                coordinator_path=coordinator, coordinator_identity=(info.st_dev, info.st_ino))
            original = installer.rename.move
            def exchange_then_quarantine(*args, **kwargs):
                original(*args, **kwargs)
                moves.append(args)
                if len(moves) == 1:
                    installed.seek(0)
                    installed.write(b'late owner bytes\n')
                    installed.truncate()
                    installed.flush()
                    quarantine(store, installer.source, tmp_path / 'late-quarantine.lock')
            monkeypatch.setattr(installer.rename, 'move', exchange_then_quarantine)
            assert not installer.rollback((change,), lambda: sources.guard(claim.id, claim.owner_id))
            assert len(moves) == 1, 'A restorative rename bypassed the new source veto'
            assert target.read_bytes() == b'before\n'
            assert (installer.recovery / '0').read_bytes() == b'late owner bytes\n'
    finally:
        store.close()


def test_byte_identical_root_replacement_before_apply_cannot_write_either_root(owned, delivery, tmp_path, monkeypatch):  # noqa: F811
    from flycode.orchestration.application import Application

    store, _, host, _, task, _, root = owned
    runtime, artifact, review, approval = delivery
    attempt = reviewer(owned, [artifact])
    service = Integration(store, runtime.root)
    previous = tmp_path / 'displaced-root'
    apply = Application.apply
    replaced = []
    def replace_then_apply(installer, changes, guard):
        claim = store.db.execute('SELECT id FROM source_claims WHERE owner_kind=? AND owner_id IN '
            '(SELECT id FROM integrations WHERE attempt_id=?)', ('integration', attempt.id)).fetchone()
        assert claim is not None and Sources(store).get(claim['id']).state == 'held'
        root.rename(previous)
        shutil.copytree(previous, root)
        assert root.stat().st_ino != previous.stat().st_ino
        replaced.append(True)
        return apply(installer, changes, guard)
    monkeypatch.setattr(Application, 'apply', replace_then_apply)
    error = None
    try:
        service.run(host, attempt.id, attempt.fence, (artifact.id,), (review.id,),
            approval_hashes={task.id: approval, attempt.task_id: approval})
    except StoreError as failure:
        error = failure
    assert replaced
    for directory in (root, previous):
        assert (directory / 'src/example.py').read_text() == 'answer = 0\n', 'Integration wrote to a replaced source root'
        assert (directory / 'user-dirty.bin').read_bytes() == b'\x00\xff user bytes\r\n'
    assert error is not None and error.code == 'source_changed'
    record = service.get(host, store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (attempt.id,)).fetchone()['id'])
    assert record.state == 'reconciling' and record.error == 'source_changed'
    assert not store._attempt(attempt.id).stop_confirmed
    assert Sources(store).get(record.source_claim_id).state == 'held'
    assert service.reconcile(host, record.id).state == 'reconciling'
    for directory in (root, previous):
        assert (directory / 'src/example.py').read_text() == 'answer = 0\n'
