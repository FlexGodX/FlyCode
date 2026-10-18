"""A duplicate caller cannot revoke a live owner or erase cancellation intent."""

import time
from threading import Thread

import pytest
from test_verification_execution import claim, executor, queued

from flycode.domain.verification_control import VerificationCommand
from flycode.orchestration.host import Host
from flycode.orchestration.store import StoreError
from flycode.verification.control import VerificationControl


def test_active_registered_duplicate_does_not_interrupt_the_real_owner(tmp_path):
    marker = tmp_path / 'running-marker'
    database, _, owner, plan, first = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        assigned = claim(store, owner, first)
        results, errors = [], []

        def work():
            connection = host.connect()
            try:
                results.append(executor(connection, assigned).run())
            except (OSError, ValueError, AssertionError) as error:
                errors.append(error)
            finally:
                connection.close()

        worker = Thread(target=work)
        worker.start()
        try:
            until = time.monotonic() + 8
            while not marker.exists() and worker.is_alive() and time.monotonic() < until:
                time.sleep(.02)
            assert marker.exists(), errors
            control = VerificationControl(store)
            original = control._execution(first.id)
            with pytest.raises((StoreError, OSError)):
                executor(store, assigned).run()
            current = control._execution(first.id)
            assert current.state == 'running' and current.fence == original.fence and current.error is None
            assert len(current.invocations) == 1 and current.invocations[0].state == 'running'
            assert worker.is_alive()
        finally:
            control = VerificationControl(store)
            latest = control.get(owner, plan.id)
            if control._execution(first.id).state in {'running', 'preparing', 'reconciling', 'evaluating'}:
                control.command(owner, plan.id, VerificationCommand(operation='cancel',
                    expected_revision=latest.revision, idempotency_key='cleanup-real-owner'))
            worker.join(10)
            store.close()
        assert not worker.is_alive() and not errors
        assert results[0].state == 'cancelled'


def test_registered_cancel_before_run_remains_durable_after_preparation_is_denied(tmp_path):
    database, _, owner, plan, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            control = VerificationControl(store)
            latest = control.get(owner, plan.id)
            control.command(owner, plan.id, VerificationCommand(operation='cancel',
                expected_revision=latest.revision, idempotency_key='cancel-before-run'))
            with pytest.raises(StoreError, match='fence'):
                executor(store, assigned).run()
            current = control._execution(first.id)
            assert getattr(current, 'cancel_requested', False), 'Cancellation intent was lost during reconciliation'
            assert current.state in {'cancelling', 'reconciling', 'cancelled'} and not current.invocations
            assert store.db.execute('SELECT tool_spent FROM verification_reservations WHERE execution_id=?',
                (first.id,)).fetchone()[0] == 0
        finally:
            store.close()
