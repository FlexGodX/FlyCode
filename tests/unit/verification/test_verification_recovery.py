"""Interrupted registered checks retain custody and cost across Host generations."""

from pathlib import Path

import pytest
from test_verification_execution import claim, executor, queued

from flycode.domain.verification_control import VerificationCommand
from flycode.orchestration.host import Host
from flycode.orchestration.store import StoreError
from flycode.verification.control import VerificationControl
from flycode.verification.custody import locked


def ledger(store, identity):
    row = store.db.execute('SELECT tool_spent,tool_reserved,uncertain FROM verification_reservations '
        'WHERE execution_id=?', (identity,)).fetchone()
    return tuple(row)


def recover(store, identity):
    execution = VerificationControl(store)._execution(identity)
    worker = executor(store, execution)
    assert hasattr(worker, 'recover'), 'Registered verification recovery is missing'
    return worker.recover()


def test_host_drain_revokes_claim_before_stop_and_restart_settles_without_project_root(tmp_path):
    database, source, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            host.drain()
            revoked = VerificationControl(store)._execution(first.id)
            assert revoked.state == 'reconciling' and revoked.fence > assigned.fence
            assert ledger(store, first.id) == (0, 1, 1)
            assert host.session.phase == 'draining'
        finally:
            store.close()
    source.rename(source.with_name('unavailable-source'))
    with Host(database, tmp_path / 'host-state') as current:
        store = current.connect()
        try:
            result = recover(store, first.id)
            assert result.state == 'interrupted' and not result.current and result.verdict is None
            assert result.ended_at is not None and result.lease_until is None
            assert ledger(store, first.id) == (0, 0, 0)
            assert store._active_execution_count() == 0
            events = store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0]
            assert recover(store, first.id) == result
            assert store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0] == events
        finally:
            store.close()


def test_restart_waits_for_exact_custody_then_keeps_failed_check_and_spending(tmp_path, monkeypatch):
    database, source, owner, plan, first = queued(tmp_path, 'def test_answer(): assert False', reruns=1)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            finish = worker._finish

            def uncertain(identity, result):
                ref = VerificationControl(store)._execution(first.id).invocations[0].process
                with locked(Path(ref.directory) / 'process.lock', identity=(ref.device, ref.inode)):
                    return finish(identity, result)

            monkeypatch.setattr(worker, '_finish', uncertain)
            original = worker.run()
            assert original.state == 'reconciling' and len(original.invocations) == 1
            saved_result = original.invocations[0].result
            current = VerificationControl(store).get(owner, plan.id)
            VerificationControl(store).command(owner, plan.id, VerificationCommand(operation='cancel',
                expected_revision=current.revision, idempotency_key='cancel-before-restart'))
        finally:
            store.close()
    source.rename(source.with_name('unavailable-source'))
    ref = original.invocations[0].process
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            before = ledger(store, first.id)
            assert before == (1, 1, 1)
            with locked(Path(ref.directory) / 'process.lock', identity=(ref.device, ref.inode)):
                with pytest.raises(StoreError, match='process_unconfirmed'):
                    recover(store, first.id)
                assert ledger(store, first.id) == before
                assert store._active_execution_count() == 1
            result = recover(store, first.id)
            assert result.state == 'cancelled' and result.cancel_requested and not result.current
            assert result.invocations[0].result == saved_result
            assert result.invocations[0].state == 'completed' and result.invocations[0].late
            assert ledger(store, first.id) == (1, 0, 0)
            assert store._active_execution_count() == 0
        finally:
            store.close()


def test_recovery_rejects_a_current_claim_without_revoking_its_owner(tmp_path):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            with pytest.raises(StoreError, match='verification_state'):
                recover(store, first.id)
            assert VerificationControl(store)._execution(first.id) == assigned
            assert executor(store, assigned).run().current
        finally:
            store.close()


@pytest.mark.parametrize('reason', ['lease', 'deadline'])
def test_expired_claim_fences_before_launch_without_spending_or_duplicate_revocation(tmp_path, reason):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True', wall=15.)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            store.clock = lambda: assigned.lease_until if reason == 'lease' else assigned.deadline_at
            control = VerificationControl(store)
            assert hasattr(control, 'expire'), 'Registered verification expiry is missing'
            control.expire(host._scope(store))
            revoked = control._execution(first.id)
            assert revoked.state == 'reconciling' and revoked.fence > assigned.fence
            assert not revoked.invocations and ledger(store, first.id) == (0, 1, 1)
            count = store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0]
            control.expire(host._scope(store))
            assert store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0] == count
            result = recover(store, first.id)
            assert result.state == 'interrupted' and not result.current
            assert ledger(store, first.id) == (0, 0, 0)
        finally:
            store.close()


def test_queued_deadline_releases_unstarted_reservation_and_remains_visible_as_blocked(tmp_path):
    database, _, _, _, first = queued(tmp_path, 'def test_answer(): assert True', wall=15.)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect(clock=lambda: first.deadline_at)
        try:
            control = VerificationControl(store)
            control.expire(host._scope(store))
            result = control._execution(first.id)
            assert result.state == 'blocked' and result.error == 'deadline'
            assert not result.invocations and result.ended_at == first.deadline_at
            assert ledger(store, first.id) == (0, 0, 0)
            before = store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0]
            control.expire(host._scope(store))
            assert store.db.execute('SELECT COUNT(*) FROM verification_events').fetchone()[0] == before
        finally:
            store.close()
