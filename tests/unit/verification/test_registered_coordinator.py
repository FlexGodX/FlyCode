"""Registered checks use the actual shared Coordinator and survive client loss."""

import json
import os
import select
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from test_verification_execution import queued
from test_verification_recovery import ledger

from flycode.domain.verification_control import (
    VerificationCommand,
    VerificationPlanDraft,
)
from flycode.orchestration.store import Store
from flycode.verification.control import VerificationControl
from flycode.verification.custody import locked, stopped
from flycode.verification.registry import VerificationRegistry


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Shared Coordinator did not reach the expected state'
        time.sleep(.03)


@contextmanager
def coordinator(database, root):
    import flycode

    environment = {**os.environ, 'PYTHONPATH': str(Path(flycode.__file__).resolve().parent.parent)}
    process = subprocess.Popen([sys.executable, '-m', 'flycode.orchestration.coordinator',
        '--database', str(database), '--state-root', str(root)], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 12)[0], 'Coordinator did not report readiness'
        line = process.stdout.readline()
        assert line, process.communicate(timeout=5)
        message = json.loads(line)
        assert 'session' in message, message
        yield process, message['session']
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            _stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate(timeout=5)
        assert not stderr, stderr


def cancel(control, owner, plan, key):
    current = control.get(owner, plan.id)
    return control.command(owner, plan.id, VerificationCommand(operation='cancel',
        expected_revision=current.revision, idempotency_key=key))


def test_shared_coordinator_executes_registered_plan_after_client_close_without_idle_flood(tmp_path):
    database, _, _, _, execution = queued(tmp_path, 'def test_answer(): assert True')
    store = Store(database)
    try:
        with coordinator(database, tmp_path / 'host-state') as (process, _):
            control = VerificationControl(store)
            until(lambda: control._execution(execution.id).state == 'completed')
            result = control._execution(execution.id)
            assert result.current and result.verdict.status == 'passed'
            assert ledger(store, execution.id) == (1, 0, 0)
            assert len(result.invocations) == 1 and process.poll() is None
            with stopped(result.invocations[0].process) as receipt:
                assert receipt.started and receipt.stop_confirmed
            before = [tuple(row) for row in store.db.execute('SELECT * FROM commands')]
            events = [tuple(row) for row in store.db.execute('SELECT * FROM verification_events')]
            time.sleep(1.2)
            assert [tuple(row) for row in store.db.execute('SELECT * FROM commands')] == before
            assert [tuple(row) for row in store.db.execute('SELECT * FROM verification_events')] == events
            for table in ('attempts', 'tasks', 'instances', 'model_calls', 'tool_calls'):
                assert store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    finally:
        store.close()


def test_shared_coordinator_stop_cancels_sleeping_registered_check_promptly(tmp_path):
    marker = tmp_path / 'started'
    database, _, owner, plan, execution = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    store = Store(database)
    try:
        with coordinator(database, tmp_path / 'host-state') as (process, _):
            control = VerificationControl(store)
            until(marker.exists)
            started = time.monotonic()
            cancel(control, owner, plan, 'stop-sleeping-check')
            until(lambda: control._execution(execution.id).state == 'cancelled', timeout=8)
            assert time.monotonic() - started < 8 and process.poll() is None
            result = control._execution(execution.id)
            assert not result.current and result.cancel_requested
            with stopped(result.invocations[0].process) as receipt:
                assert receipt.started and receipt.stop_confirmed
            assert ledger(store, execution.id) == (1, 0, 0)
    finally:
        store.close()


def test_killed_coordinator_recovers_stopped_check_without_source_or_reexecution(tmp_path):
    marker, counter = tmp_path / 'started', tmp_path / 'executions'
    database, source, _, _, execution = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    with Path({str(counter)!r}).open("a") as stream: stream.write("executed\\n")\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    store = Store(database)
    try:
        with coordinator(database, tmp_path / 'host-state') as (old, session):
            until(marker.exists)
            old.kill()
            old.wait(timeout=5)
        control = VerificationControl(store)
        previous = control._execution(execution.id)
        source.rename(source.with_name('unavailable-source'))
        with coordinator(database, tmp_path / 'host-state') as (current, restarted):
            assert restarted['generation'] == session['generation'] + 1
            until(lambda: control._execution(execution.id).state == 'interrupted')
            result = control._execution(execution.id)
            assert result.fence > previous.fence and not result.current and result.verdict is None
            assert ledger(store, execution.id) == (1, 0, 0)
            assert len(result.invocations) == 1 and current.poll() is None
            with stopped(result.invocations[0].process) as receipt:
                assert receipt.started and receipt.stop_confirmed and receipt.reason == 'coordinator_lost'
            assert counter.read_text() == 'executed\n'
    finally:
        store.close()


def test_shared_coordinator_never_admits_fifth_registered_check_until_a_slot_settles(tmp_path):
    marker = tmp_path / 'started'
    database, _, owner, plan, first = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    with Path({str(marker)!r}).open("a") as stream: stream.write("executed\\n")\n'
        '    time.sleep(60)\n')
    store = Store(database)
    try:
        registry, control = VerificationRegistry(store), VerificationControl(store)
        executions = [first]
        plans = [plan]
        for number in range(4):
            registered = registry.create_plan(owner, VerificationPlanDraft(subject_ids=plan.subject_ids,
                profile_id=plan.profile_id, profile_version=1, budget=plan.budget,
                idempotency_key='create-' + str(number)))
            plans.append(registered)
            executions.append(control.command(owner, registered.id, VerificationCommand(operation='start',
                expected_revision=1, idempotency_key='start-' + str(number))))
        with coordinator(database, tmp_path / 'host-state'):
            until(lambda: marker.exists() and len(marker.read_text().splitlines()) == 4)
            assert store._active_execution_count() == 4
            waiting = [item for item in executions if control._execution(item.id).state == 'queued']
            assert len(waiting) == 1
            active = next(item for item in executions if item.id != waiting[0].id)
            cancel(control, owner, next(item for item in plans if item.id == active.plan_id), 'free-one-slot')
            until(lambda: len(marker.read_text().splitlines()) == 5)
            assert control._execution(active.id).state == 'cancelled'
            assert store._active_execution_count() == 4
            for registered, item in zip(plans, executions):
                if control._execution(item.id).state != 'cancelled':
                    cancel(control, owner, registered, 'stop-' + item.id)
            until(lambda: all(control._execution(item.id).state == 'cancelled' for item in executions))
            assert store._active_execution_count() == 0
    finally:
        store.close()


def test_graceful_coordinator_drain_retains_cost_until_restart_settles_exact_group(tmp_path):
    marker = tmp_path / 'started'
    database, _, _, _, execution = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    store = Store(database)
    try:
        with coordinator(database, tmp_path / 'host-state') as (old, _):
            until(marker.exists)
            old.terminate()
            until(lambda: old.poll() is not None)
        control = VerificationControl(store)
        pending = control._execution(execution.id)
        assert pending.state == 'reconciling' and not pending.current
        assert ledger(store, execution.id) == (1, 0, 1)
        with stopped(pending.invocations[0].process) as receipt:
            assert receipt.started and receipt.stop_confirmed
        with coordinator(database, tmp_path / 'host-state'):
            until(lambda: control._execution(execution.id).state == 'interrupted')
            assert ledger(store, execution.id) == (1, 0, 0)
    finally:
        store.close()


def test_changed_registered_subject_blocks_queued_plan_without_process_or_spend(tmp_path):
    database, _, owner, plan, execution = queued(tmp_path, 'def test_answer(): assert True')
    store = Store(database)
    try:
        registry = VerificationRegistry(store)
        root = registry.subject_root(owner, plan.subject_ids['target'])
        (root / 'test_answer.py').write_text('def test_answer(): assert False')
        with coordinator(database, tmp_path / 'host-state'):
            control = VerificationControl(store)
            until(lambda: control._execution(execution.id).state == 'blocked')
            result = control._execution(execution.id)
            assert result.error is not None and not result.invocations and not result.current
            assert ledger(store, execution.id) == (0, 0, 0)
    finally:
        store.close()


@pytest.mark.parametrize('obstruction', ['lock', 'directory'])
def test_recovery_records_custody_blocker_without_event_flood_and_retries_restoration(tmp_path, obstruction):
    marker = tmp_path / 'started'
    database, _, _, _, execution = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    store = Store(database)
    try:
        control = VerificationControl(store)
        with coordinator(database, tmp_path / 'host-state') as (old, _):
            until(marker.exists)
            old.kill()
            old.wait(timeout=5)
        original = control._execution(execution.id)
        root = Path(original.coordinator_root.path)
        def group_stopped():
            try:
                with stopped(original.invocations[0].process):
                    return True
            except (OSError, ValueError):
                return False
        until(group_stopped)
        with ExitStack() as locks:
            saved = root.with_name(root.name + '-saved')
            if obstruction == 'lock':
                locks.enter_context(locked(root / 'coordinator.lock', identity=original.coordinator_identity))
            else:
                root.rename(saved)
                root.mkdir()
                for item in saved.iterdir():
                    item.rename(root / item.name)
            with coordinator(database, tmp_path / 'host-state'):
                reason = 'coordinator_running' if obstruction == 'lock' else 'ownership_unknown'
                until(lambda: control._execution(execution.id).error == reason)
                before = [tuple(row) for row in store.db.execute('SELECT * FROM verification_events')]
                assert ledger(store, execution.id) == (1, 0, 1)
                time.sleep(1.3)
                assert [tuple(row) for row in store.db.execute('SELECT * FROM verification_events')] == before
                locks.close()
                if obstruction == 'directory':
                    for item in root.iterdir():
                        item.rename(saved / item.name)
                    root.rmdir()
                    saved.rename(root)
                until(lambda: control._execution(execution.id).state == 'interrupted', timeout=5)
                assert ledger(store, execution.id) == (1, 0, 0)
    finally:
        store.close()
