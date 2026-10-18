"""Registered execution owns real pytest processes and cumulative spending."""

import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Thread

import pytest

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification import CheckSpec
from flycode.domain.verification_control import (
    VerificationCommand,
    VerificationPlanDraft,
    VerificationProfileDraft,
)
from flycode.orchestration.host import Host
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, StoreError
from flycode.verification.control import VerificationControl
from flycode.verification.custody import stopped
from flycode.verification.registry import VerificationRegistry


def queued(tmp_path, code, *, reruns=0, tools=6, wall=900.):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'test_answer.py').write_text(code)
    store = Store(tmp_path / 'state.sqlite')
    try:
        project = Projects(store).register(source, actor_id='owner')
        owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
        registry = VerificationRegistry(store)
        subject = registry.register_subject(owner, project.id)
        profile = registry.register_profile(owner, project.id, VerificationProfileDraft(title='Original tests',
            checks=[CheckSpec(id='selected', timeout=10.)], reruns=reruns))
        plan = registry.create_plan(owner, VerificationPlanDraft(subject_ids={'target': subject.id},
            profile_id=profile.id, profile_version=profile.version,
            budget={'max_tool_calls': tools, 'max_attempts': 3, 'wall_seconds': wall}, idempotency_key='create'))
        execution = VerificationControl(store).command(owner, plan.id,
            VerificationCommand(operation='start', expected_revision=1, idempotency_key='start'))
        return store.path, source, owner, plan, execution
    finally:
        store.close()


def runtime(owner):
    return owner.model_copy(update={'role': 'runtime', 'actor_id': 'trusted-host'})


def executor(store, execution):
    assert importlib.util.find_spec('flycode.verification.execution') is not None, 'Registered executor is missing'
    from flycode.verification.execution import VerificationExecutor

    return VerificationExecutor(store, execution.id, execution.fence)


def claim(store, owner, execution):
    control = VerificationControl(store)
    assert hasattr(control, 'claim'), 'Registered execution ownership is missing'
    return control.claim(runtime(owner), execution.id)


@pytest.mark.parametrize('reruns', [0, 1])
def test_transient_authority_loss_at_real_check_finish_never_becomes_current(tmp_path, monkeypatch, reruns):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True', reruns=reruns)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            finish = worker._finish
            queue_owner = owner.model_copy(update={'global_queue_control': True})

            def pause_at_finish(identity, result):
                store.pause_queue(queue_owner, True, key='transient-pause')
                try:
                    finish(identity, result)
                finally:
                    store.pause_queue(queue_owner, False, key='transient-resume')

            monkeypatch.setattr(worker, '_finish', pause_at_finish)
            result = worker.run()
            assert not result.current and result.state in {'interrupted', 'cancelled'}
            assert len(result.invocations) == 1 and result.invocations[0].late
            with stopped(result.invocations[0].process) as receipt:
                assert receipt.started and receipt.stop_confirmed
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (first.id,)).fetchone()
            assert (ledger['tool_spent'], ledger['tool_reserved'], ledger['uncertain']) == (1, 0, 0)
        finally:
            store.close()


def test_clock_rollback_denies_claim_without_spending_or_ownership(tmp_path):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            store.clock = lambda: first.created_at - 1
            with pytest.raises(StoreError, match='deadline'):
                claim(store, owner, first)
            assert VerificationControl(store)._execution(first.id) == first
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (first.id,)).fetchone()
            assert (ledger['tool_spent'], ledger['tool_reserved'], ledger['uncertain']) == (0, 1, 0)
        finally:
            store.close()


def test_clock_rollback_denies_rerun_after_real_completion_without_resetting_history(tmp_path):
    database, _, owner, plan, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            result = executor(store, claim(store, owner, first)).run()
            assert result.current and result.verdict.status == 'passed'
            control = VerificationControl(store)
            current = control.get(owner, plan.id)
            store.clock = lambda: first.created_at - 1
            with pytest.raises(StoreError, match='deadline'):
                control.command(owner, plan.id, VerificationCommand(operation='rerun',
                    expected_revision=current.revision, idempotency_key='backward-rerun'))
            assert control.get(owner, plan.id) == current
            assert control._execution(first.id) == result
            assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1
            assert store.db.execute('SELECT tool_spent FROM verification_reservations').fetchone()[0] == 1
        finally:
            store.close()


def test_registered_native_execution_charges_every_check_without_fake_agent_attempts(tmp_path):
    database, source, owner, plan, queued_execution = queued(tmp_path, 'def test_answer(): assert True', reruns=1)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, queued_execution)
            assert assigned.state == 'preparing' and assigned.fence == 1
            result = executor(store, assigned).run()
            assert result.state == 'completed' and result.verdict.status == 'passed' and result.current
            assert result.fence == assigned.fence and result.ended_at is not None
            assert len(result.invocations) == 2
            assert all(call.result.plan_id == plan.id and call.result.check_id == 'selected'
                and call.state == 'completed' and call.process == call.result.process
                for call in result.invocations)
            for call in result.invocations:
                with stopped(call.process) as receipt:
                    assert receipt.stop_confirmed and receipt.started
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?',
                (assigned.id,)).fetchone()
            assert (ledger['tool_spent'], ledger['tool_reserved'], ledger['uncertain']) == (2, 0, 0)
            for table in ('attempts', 'tasks', 'instances', 'model_calls', 'tool_calls'):
                assert store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
            assert (source / 'test_answer.py').read_text() == 'def test_answer(): assert True'
            bundle = Path(result.coordinator_root.path) / 'evidence' / result.verdict.evidence_id
            assert json.loads((bundle / 'plan.json').read_text())['id'] == plan.id
            assert json.loads((bundle / 'manifest.json').read_text())['complete']
        finally:
            store.close()


def test_registered_rerun_keeps_failed_evidence_and_cumulative_tool_limit(tmp_path):
    marker = tmp_path / 'attempt-marker'
    database, _, owner, plan, first = queued(tmp_path,
        'from pathlib import Path\ndef test_answer():\n'
        f'    marker = Path({str(marker)!r})\n'
        '    previous = marker.exists()\n    marker.touch()\n    assert previous\n', tools=2)
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            control = VerificationControl(store)
            failed = executor(store, claim(store, owner, first)).run()
            assert failed.state == 'completed' and failed.verdict.status == 'failed'
            old_json = store.db.execute('SELECT data FROM verification_executions WHERE id=?', (failed.id,)).fetchone()[0]
            current = control.get(owner, plan.id)
            second = control.command(owner, plan.id, VerificationCommand(operation='rerun',
                expected_revision=current.revision, idempotency_key='rerun'))
            passed = executor(store, claim(store, owner, second)).run()
            assert passed.verdict.status == 'passed' and passed.current
            assert passed.verdict.evidence_id != failed.verdict.evidence_id and passed.deadline_at == failed.deadline_at
            assert store.db.execute('SELECT data FROM verification_executions WHERE id=?', (failed.id,)).fetchone()[0] == old_json
            current = control.get(owner, plan.id)
            with pytest.raises(StoreError, match='budget'):
                control.command(owner, plan.id, VerificationCommand(operation='rerun',
                    expected_revision=current.revision, idempotency_key='exhausted'))
            assert store.db.execute('SELECT SUM(tool_spent) FROM verification_reservations').fetchone()[0] == 2
        finally:
            store.close()


def test_registered_two_connections_cannot_claim_the_same_execution(tmp_path):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    barrier = Barrier(2)
    with Host(database, tmp_path / 'host-state') as host:
        def assign(_):
            store = host.connect()
            try:
                barrier.wait(timeout=5)
                try:
                    return claim(store, owner, first)
                except StoreError as error:
                    return error.code
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, range(2)))
        winners = [result for result in results if not isinstance(result, str)]
        assert len(winners) == 1 and [result for result in results if isinstance(result, str)] == ['verification_state']
        store = host.connect()
        try:
            assert executor(store, winners[0]).run().verdict.status == 'passed'
        finally:
            store.close()


def test_registered_durable_cancel_stops_actual_sleeping_group_before_release(tmp_path):
    marker = tmp_path / 'running-marker'
    database, source, owner, plan, first = queued(tmp_path,
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
            deadline = time.monotonic() + 8
            while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(.02)
            assert marker.exists(), errors
            control = VerificationControl(store)
            current = control.get(owner, plan.id)
            cancelling = control.command(owner, plan.id, VerificationCommand(operation='cancel',
                expected_revision=current.revision, idempotency_key='cancel'))
            assert cancelling.state == 'cancelling'
            worker.join(8)
            assert not worker.is_alive() and not errors
            result = results[0]
            assert result.state == 'cancelled' and result.verdict.status == 'cancelled' and not result.current
            assert len(result.invocations) == 1
            with stopped(result.invocations[0].process) as receipt:
                assert receipt.started and receipt.stop_confirmed and receipt.reason == 'cancelled'
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (first.id,)).fetchone()
            assert (ledger['tool_spent'], ledger['tool_reserved'], ledger['uncertain']) == (1, 0, 0)
            assert (source / 'test_answer.py').read_text().endswith('time.sleep(60)\n')
        finally:
            if worker.is_alive():
                current = VerificationControl(store).get(owner, plan.id)
                VerificationControl(store).command(owner, plan.id, VerificationCommand(operation='cancel',
                    expected_revision=current.revision, idempotency_key='cleanup'))
                worker.join(10)
            store.close()


def test_registered_stale_lease_denies_execution_before_any_runner(tmp_path):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            store.clock = lambda: assigned.lease_until
            with pytest.raises(StoreError, match='fence'):
                executor(store, assigned).run()
            assert not VerificationControl(store)._execution(first.id).invocations
            assert store.db.execute('SELECT tool_spent FROM verification_reservations WHERE execution_id=?',
                (first.id,)).fetchone()[0] == 0
        finally:
            store.close()


def test_registered_owner_edit_keeps_historical_pass_but_cannot_be_current(tmp_path, monkeypatch):
    database, source, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            finish = worker._finish

            def edited(identity, result):
                (source / 'test_answer.py').write_text('def test_answer(): assert False')
                return finish(identity, result)

            monkeypatch.setattr(worker, '_finish', edited)
            result = worker.run()
            assert result.state == 'completed' and result.verdict.status == 'passed'
            assert not result.current and result.error == 'subject_changed'
            assert (source / 'test_answer.py').read_text() == 'def test_answer(): assert False'
        finally:
            store.close()


def test_registered_unconfirmed_custody_retains_capacity_and_reservation(tmp_path, monkeypatch):
    from flycode.verification.custody import locked

    database, _, owner, plan, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            finish = worker._finish

            def held(identity, result):
                ref = VerificationControl(store)._execution(first.id).invocations[0].process
                with locked(Path(ref.directory) / 'process.lock', identity=(ref.device, ref.inode)):
                    return finish(identity, result)

            monkeypatch.setattr(worker, '_finish', held)
            result = worker.run()
            assert result.state == 'reconciling' and not result.current
            assert result.invocations[0].state == 'uncertain'
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (first.id,)).fetchone()
            assert ledger['uncertain'] and ledger['tool_spent'] == 1
            current = VerificationControl(store).get(owner, plan.id)
            with pytest.raises(StoreError, match='verification_state'):
                VerificationControl(store).command(owner, plan.id, VerificationCommand(operation='rerun',
                    expected_revision=current.revision, idempotency_key='deny-unsettled-rerun'))
        finally:
            store.close()


def test_registered_shared_host_caps_claims_at_four_even_for_one_project(tmp_path):
    database, _, owner, plan, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            control, registry = VerificationControl(store), VerificationRegistry(store)
            requests = [first]
            for number in range(4):
                registered = registry.create_plan(owner, VerificationPlanDraft(subject_ids=plan.subject_ids,
                    profile_id=plan.profile_id, profile_version=plan.profile_version,
                    budget=plan.budget, idempotency_key='create-' + str(number)))
                requests.append(control.command(owner, registered.id, VerificationCommand(operation='start',
                    expected_revision=1, idempotency_key='start-' + str(number))))
            assigned = [claim(store, owner, item) for item in requests[:4]]
            with pytest.raises(StoreError, match='instance_limit'):
                claim(store, owner, requests[4])
            for item in assigned:
                assert executor(store, item).run().state == 'completed'
            assert executor(store, claim(store, owner, requests[4])).run().verdict.status == 'passed'
        finally:
            store.close()


@pytest.mark.parametrize('replace', ['lock', 'directory'])
def test_registered_changed_coordinator_custody_cannot_publish_current_pass(tmp_path, monkeypatch, replace):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            finish = worker._finish

            def replaced(identity, result):
                finish(identity, result)
                root = Path(VerificationControl(store)._execution(first.id).coordinator_root.path)
                if replace == 'lock':
                    (root / 'coordinator.lock').rename(root / 'old-coordinator.lock')
                    (root / 'coordinator.lock').touch()
                else:
                    saved = root.with_name(root.name + '-moved')
                    root.rename(saved)
                    root.mkdir()
                    for item in saved.iterdir():
                        item.rename(root / item.name)

            monkeypatch.setattr(worker, '_finish', replaced)
            result = worker.run()
            assert not result.current and result.state == 'reconciling'
            assert result.error == 'ownership_unknown'
            assert store.db.execute('SELECT uncertain FROM verification_reservations WHERE execution_id=?',
                (first.id,)).fetchone()[0] == 1
        finally:
            store.close()


def test_registered_byte_identical_owner_folder_replacement_invalidates_current_authority(tmp_path, monkeypatch):
    database, source, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            original = worker._current_source

            def replaced(registry, record, project):
                result = original(registry, record, project)
                saved = source.with_name('old-project')
                source.rename(saved)
                source.mkdir()
                for item in saved.iterdir():
                    item.rename(source / item.name)
                return result

            monkeypatch.setattr(worker, '_current_source', replaced)
            result = worker.run()
            assert not result.current and result.error == 'project_changed'
        finally:
            store.close()


def test_registered_setup_failure_after_claim_records_reconciliation_reason(tmp_path):
    database, source, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            source.rename(source.with_name('removed-project'))
            with pytest.raises((OSError, StoreError)):
                executor(store, assigned).run()
            result = VerificationControl(store)._execution(first.id)
            assert result.state == 'reconciling' and result.error is not None
            assert not result.invocations
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (first.id,)).fetchone()
            assert ledger['tool_spent'] == 0 and ledger['tool_reserved'] == 1 and ledger['uncertain']
        finally:
            store.close()


def test_registered_replacing_coordinator_lock_before_go_never_starts_the_test(tmp_path, monkeypatch):
    marker = tmp_path / 'unexpected-start'
    database, _, owner, _, first = queued(tmp_path,
        'from pathlib import Path\ndef test_answer():\n'
        f'    Path({str(marker)!r}).touch()\n')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            worker = executor(store, claim(store, owner, first))
            original = worker._process

            def replaced(identity, ref, *, admit):
                result = original(identity, ref, admit=admit)
                if not admit:
                    root = Path(VerificationControl(store)._execution(first.id).coordinator_root.path)
                    (root / 'coordinator.lock').rename(root / 'old-coordinator.lock')
                    (root / 'coordinator.lock').touch()
                return result

            monkeypatch.setattr(worker, '_process', replaced)
            result = worker.run()
            assert not marker.exists() and not result.current and result.state == 'reconciling'
            assert all(not call.result.process_started for call in result.invocations)
        finally:
            store.close()


def test_registered_duplicate_executor_cannot_overwrite_a_completed_result(tmp_path):
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            assigned = claim(store, owner, first)
            result = executor(store, assigned).run()
            original = result.model_dump_json()
            with pytest.raises(StoreError, match='fence'):
                executor(store, assigned).run()
            assert VerificationControl(store)._execution(first.id).model_dump_json() == original
        finally:
            store.close()
