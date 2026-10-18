"""Immutable plans and replay-safe commands share durable accounting."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification_control import VerificationProfileDraft
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import Store, StoreError
from flycode.verification.registry import VerificationRegistry


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'test_answer.py').write_text('def test_answer():\n    assert 1 == 1\n')
    store = Store(tmp_path / 'state.sqlite', clock=lambda: 100.)
    project = Projects(store).register(source, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
    registry = VerificationRegistry(store)
    subject = registry.register_subject(owner, project.id)
    profile = registry.register_profile(owner, project.id, VerificationProfileDraft(title='Original tests'))
    try:
        yield store, owner, registry, subject, profile, source
    finally:
        store.close()


def draft(subject, profile, **changes):
    from flycode.domain import verification_control as contracts

    assert hasattr(contracts, 'VerificationPlanDraft'), 'Registered executable plan contract is missing'
    values = {'subject_ids': {'target': subject.id}, 'profile_id': profile.id, 'profile_version': profile.version,
        'budget': {'max_tool_calls': 6, 'max_attempts': 3, 'wall_seconds': 900.}, 'idempotency_key': 'create-plan'}
    return contracts.VerificationPlanDraft(**{**values, **changes})


def control(store):
    assert importlib.util.find_spec('flycode.verification.control') is not None, 'Verification commands are missing'
    from flycode.verification.control import VerificationControl

    return VerificationControl(store)


def command(operation, revision, key):
    from flycode.domain.verification_control import VerificationCommand

    return VerificationCommand(operation=operation, expected_revision=revision, idempotency_key=key)


def test_plan_binds_exact_registered_bytes_without_executing_or_mutating_profile(inputs):
    store, owner, registry, subject, profile, source = inputs
    request = draft(subject, profile)
    assert hasattr(registry, 'create_plan'), 'Executable plan registration is missing'
    plan = registry.create_plan(owner, request)
    (source / 'test_answer.py').write_text('raise RuntimeError("changed owner bytes")\n')
    saved = control(store).get(owner, plan.id)
    assert saved.plan.subject_hash == subject.snapshot.subject_hash
    assert saved.plan.subjects['target'].model_dump(mode='json') == subject.snapshot.model_dump(mode='json')
    assert saved.plan.checks[0].subject_hash == subject.snapshot.subject_hash
    assert registry.profile(owner, profile.id).draft.checks[0].subject_hash is None
    assert saved.state == 'draft' and saved.revision == 1 and saved.deadline_at is None
    for table in ('workflows', 'attempts', 'workers', 'model_calls', 'tool_calls', 'verification_executions'):
        assert store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    assert registry.create_plan(owner, request).model_dump(mode='json') == plan.model_dump(mode='json')


def test_create_and_start_replays_preserve_first_result_and_reject_changed_payload(inputs):
    store, owner, registry, subject, profile, _ = inputs
    request = draft(subject, profile)
    plan = registry.create_plan(owner, request)
    ctl = control(store)
    start = command('start', plan.revision, 'start-plan')
    first = ctl.command(owner, plan.id, start)
    assert ctl.command(owner, plan.id, start) == first
    assert first.state == 'queued' and first.number == 1
    with pytest.raises(StoreError, match='idempotency'):
        registry.create_plan(owner, draft(subject, profile, budget={**request.budget.model_dump(), 'max_tool_calls': 5}))
    with pytest.raises(StoreError, match='idempotency'):
        ctl.command(owner, plan.id, command('cancel', 2, 'start-plan'))
    with pytest.raises(StoreError, match='revision'):
        ctl.command(owner, plan.id, command('start', 1, 'different-key'))
    assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1
    events = ctl.events(owner, plan.id)
    assert [event.kind for event in events] == ['plan_created', 'execution_queued']
    assert len({event.sequence for event in events}) == 2


def test_two_real_connections_racing_start_create_exactly_one_execution(inputs):
    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile))
    barrier = Barrier(2)

    def start(key):
        connection = Store(store.path, clock=store.clock)
        try:
            ctl = control(connection)
            barrier.wait(timeout=5)
            try:
                return ctl.command(owner, plan.id, command('start', 1, key))
            except StoreError as error:
                return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, ('race-a', 'race-b')))
    winners = [result for result in results if not isinstance(result, str)]
    assert len(winners) == 1 and [result for result in results if isinstance(result, str)] == ['revision']
    assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1
    assert control(store).get(owner, plan.id).current_execution_id == winners[0].id


def test_foreign_and_read_only_scopes_cannot_start_or_see_plan_events(inputs):
    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile))
    ctl = control(store)
    foreign = AccessScope(actor_id='foreign', project_ids={'other'}, role='owner')
    reader = AccessScope(actor_id='reader', project_ids=owner.project_ids, role='reader')
    for scope in (foreign, reader):
        with pytest.raises(StoreError, match='scope'):
            ctl.command(scope, plan.id, command('start', 1, 'denied-start'))
    for read in (ctl.get, ctl.events):
        with pytest.raises(StoreError, match='scope'):
            read(foreign, plan.id)
    assert not store.db.execute('SELECT 1 FROM verification_executions').fetchone()


def test_cancel_queued_then_rerun_is_explicit_and_keeps_deadline_and_history(inputs):
    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile))
    ctl = control(store)
    first = ctl.command(owner, plan.id, command('start', 1, 'start'))
    deadline = ctl.get(owner, plan.id).deadline_at
    store.clock = lambda: 110.
    cancel = command('cancel', 2, 'cancel')
    cancelled = ctl.command(owner, plan.id, cancel)
    assert cancelled.id == first.id and cancelled.state == 'cancelled'
    assert ctl.command(owner, plan.id, cancel) == cancelled
    assert store.db.execute('SELECT tool_reserved FROM verification_reservations WHERE execution_id=?',
        (first.id,)).fetchone()[0] == 0
    with pytest.raises(StoreError, match='verification_state'):
        ctl.command(owner, plan.id, command('start', 3, 'implicit-restart'))
    second = ctl.command(owner, plan.id, command('rerun', 3, 'explicit-rerun'))
    assert second.id != first.id and second.number == 2 and second.deadline_at == deadline
    assert ctl.get(owner, plan.id).deadline_at == deadline
    from flycode.domain.verification_control import VerificationExecution

    old = VerificationExecution.model_validate_json(store.db.execute(
        'SELECT data FROM verification_executions WHERE id=?', (first.id,)).fetchone()[0])
    assert old == cancelled
    assert [event.kind for event in ctl.events(owner, plan.id)] == [
        'plan_created', 'execution_queued', 'execution_cancelled', 'execution_queued']


def test_running_cancel_retains_reservation_until_runner_confirms_settlement(inputs):
    from flycode.orchestration.store import changed

    store, owner, registry, subject, profile, source = inputs
    plan = registry.create_plan(owner, draft(subject, profile))
    ctl = control(store)
    first = ctl.command(owner, plan.id, command('start', 1, 'start'))
    # A durable pre-existing runner is the input to the command under test.
    running = changed(first, state='running', fence=1)
    store.db.execute('UPDATE verification_executions SET state=?,data=? WHERE id=?',
        ('running', running.model_dump_json(), first.id))
    source.rename(source.with_name('removed-source'))
    cancelled = ctl.command(owner, plan.id, command('cancel', 2, 'cancel-after-root-loss'))
    assert cancelled.state == 'cancelling' and cancelled.ended_at is None
    assert store.db.execute('SELECT tool_reserved FROM verification_reservations WHERE execution_id=?',
        (first.id,)).fetchone()[0] == 1
    with pytest.raises((OSError, StoreError)):
        ctl.command(owner, plan.id, command('rerun', 3, 'cannot-rerun'))
    assert store.db.execute('SELECT count(*) FROM verification_executions').fetchone()[0] == 1


def test_rerun_does_not_reset_spent_tools_or_overwrite_previous_result(inputs):
    from flycode.orchestration.store import changed

    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile,
        budget={'max_tool_calls': 1, 'max_attempts': 3, 'wall_seconds': 900.}))
    ctl = control(store)
    first = ctl.command(owner, plan.id, command('start', 1, 'start'))
    # Seed an already settled attempt/ledger, independently of command code.
    completed = changed(first, state='completed', ended_at=101.)
    current = changed(ctl.get(owner, plan.id), state='completed', revision=3)
    store.db.execute('UPDATE verification_executions SET state=?,data=? WHERE id=?',
        ('completed', completed.model_dump_json(), first.id))
    store.db.execute('UPDATE verification_plans SET data=? WHERE id=?', (current.model_dump_json(), plan.id))
    store.db.execute('UPDATE verification_reservations SET tool_reserved=0,tool_spent=1 WHERE execution_id=?', (first.id,))
    before = [tuple(row) for row in store.db.execute('SELECT * FROM verification_executions')]
    with pytest.raises(StoreError, match='budget'):
        ctl.command(owner, plan.id, command('rerun', 3, 'budget-cannot-reset'))
    assert [tuple(row) for row in store.db.execute('SELECT * FROM verification_executions')] == before
    assert store.db.execute('SELECT tool_spent FROM verification_reservations').fetchone()[0] == 1


def test_attempt_limit_and_original_deadline_survive_cancelled_attempts(inputs):
    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile,
        budget={'max_tool_calls': 6, 'max_attempts': 1, 'wall_seconds': 5.}))
    ctl = control(store)
    ctl.command(owner, plan.id, command('start', 1, 'start'))
    ctl.command(owner, plan.id, command('cancel', 2, 'cancel'))
    with pytest.raises(StoreError, match='attempt_limit'):
        ctl.command(owner, plan.id, command('rerun', 3, 'attempt-limit'))
    other = registry.create_plan(owner, draft(subject, profile, idempotency_key='other-plan',
        budget={'max_tool_calls': 6, 'max_attempts': 3, 'wall_seconds': 5.}))
    ctl.command(owner, other.id, command('start', 1, 'other-start'))
    ctl.command(owner, other.id, command('cancel', 2, 'other-cancel'))
    store.clock = lambda: 106.
    with pytest.raises(StoreError, match='deadline'):
        ctl.command(owner, other.id, command('rerun', 3, 'deadline-cannot-reset'))


def test_hostile_profile_and_changed_environment_do_not_queue_execution(inputs, monkeypatch):
    store, owner, registry, subject, profile, _ = inputs
    hostile = registry.register_profile(owner, subject.project_id,
        VerificationProfileDraft(title='Hostile source', trust='untrusted'))
    plan = registry.create_plan(owner, draft(subject, hostile))
    ctl = control(store)
    with pytest.raises(StoreError, match='sandbox_unavailable'):
        ctl.command(owner, plan.id, command('start', 1, 'hostile'))
    safe = registry.create_plan(owner, draft(subject, profile, idempotency_key='safe'))
    from flycode.verification import control as module

    monkeypatch.setattr(module, 'environment_identity', lambda: {'changed': True})
    with pytest.raises(StoreError, match='verification_environment_changed'):
        ctl.command(owner, safe.id, command('start', 1, 'different-environment'))
    assert not store.db.execute('SELECT 1 FROM verification_executions').fetchone()


def test_foreign_subject_cannot_be_bound_using_two_authorized_projects(inputs, tmp_path):
    store, owner, registry, subject, profile, _ = inputs
    other = tmp_path / 'other-project'
    other.mkdir()
    project = Projects(store).register(other, actor_id='owner')
    owner = owner.model_copy(update={'project_ids': owner.project_ids | {project.id}})
    foreign = registry.register_subject(owner, project.id)
    with pytest.raises(StoreError, match='verification_subject_scope'):
        registry.create_plan(owner, draft(foreign, profile))
    with pytest.raises(StoreError, match='verification_profile_version'):
        registry.create_plan(owner, draft(subject, profile, profile_version=2))
    with pytest.raises(StoreError, match='not_found'):
        registry.create_plan(owner, draft(subject, profile, subject_ids={'target': 'f' * 32}))
    assert not store.db.execute('SELECT 1 FROM verification_plans').fetchone()


def test_diagnostic_reruns_reserve_every_planned_invocation(inputs):
    store, owner, registry, subject, _, _ = inputs
    profile = registry.register_profile(owner, subject.project_id,
        VerificationProfileDraft(title='Diagnostic repeats', reruns=2))
    too_small = registry.create_plan(owner, draft(subject, profile,
        budget={'max_tool_calls': 2, 'max_attempts': 3, 'wall_seconds': 900.}))
    ctl = control(store)
    with pytest.raises(StoreError, match='budget'):
        ctl.command(owner, too_small.id, command('start', 1, 'small'))
    enough = registry.create_plan(owner, draft(subject, profile, idempotency_key='enough'))
    execution = ctl.command(owner, enough.id, command('start', 1, 'start'))
    assert store.db.execute('SELECT tool_reserved FROM verification_reservations WHERE execution_id=?',
        (execution.id,)).fetchone()[0] == 3
    assert store.db.execute('SELECT count(*) FROM tool_calls').fetchone()[0] == 0


def test_command_event_failure_rolls_back_execution_reservation_revision_and_replay(inputs, monkeypatch):
    store, owner, registry, subject, profile, _ = inputs
    plan = registry.create_plan(owner, draft(subject, profile))
    ctl = control(store)

    def unavailable(*args, **kwargs):
        raise RuntimeError('event storage unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(ctl, '_event', unavailable)
        with pytest.raises(RuntimeError, match='event storage unavailable'):
            ctl.command(owner, plan.id, command('start', 1, 'atomic-start'))
    assert ctl.get(owner, plan.id) == plan
    for table in ('verification_executions', 'verification_reservations'):
        assert not store.db.execute('SELECT 1 FROM ' + table).fetchone()
    assert not store.db.execute('SELECT 1 FROM commands WHERE key=?', ('atomic-start',)).fetchone()
    assert ctl.command(owner, plan.id, command('start', 1, 'atomic-start')).number == 1


def test_create_lost_response_replay_needs_no_fresh_source_or_storage_access(inputs):
    store, owner, registry, subject, profile, source = inputs
    request = draft(subject, profile)
    plan = registry.create_plan(owner, request)
    source.rename(source.with_name('removed-project'))
    storage = store.path.with_name(store.path.name + '.verification')
    storage.rename(storage.with_name('removed-verification'))
    assert registry.create_plan(owner, request).model_dump(mode='json') == plan.model_dump(mode='json')
    reader = owner.model_copy(update={'role': 'reader'})
    with pytest.raises(StoreError, match='scope'):
        registry.create_plan(reader, request)
    with pytest.raises((OSError, StoreError)):
        control(store).command(owner, plan.id, command('start', 1, 'fresh-execution-denied'))


def test_schema21_failure_keeps_catalogue_and_prior_command_history(inputs, monkeypatch):
    import sqlite3

    store, _, _, subject, profile, _ = inputs
    before = {name: [tuple(row) for row in store.db.execute('SELECT * FROM ' + name)] for name in (
        'canonical_projects', 'project_grants', 'verification_subjects', 'verification_profiles', 'verification_storage', 'commands')}
    new_tables = ('verification_events', 'verification_reservations', 'verification_executions', 'verification_plans')
    for table in new_tables:
        store.db.execute('DROP TABLE ' + table)
    store.db.execute('PRAGMA user_version=20')
    connect = sqlite3.connect

    def deny_version(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, value, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and name == 'user_version' and value == '21' else sqlite3.SQLITE_OK)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', deny_version)
        with pytest.raises(sqlite3.DatabaseError):
            Store(store.path)
    assert store.db.execute('PRAGMA user_version').fetchone()[0] == 20
    for table in new_tables:
        assert not store.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone()
    assert {name: [tuple(row) for row in store.db.execute('SELECT * FROM ' + name)] for name in before} == before
    reopened = Store(store.path, clock=store.clock)
    try:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == 21
        assert reopened.db.execute('SELECT id FROM verification_subjects').fetchone()[0] == subject.id
        assert reopened.db.execute('SELECT id FROM verification_profiles').fetchone()[0] == profile.id
    finally:
        reopened.close()
