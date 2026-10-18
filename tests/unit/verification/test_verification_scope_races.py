"""New projects cannot break metadata expiry or atomic Host draining."""

from contextlib import contextmanager

import pytest
from test_verification_execution import claim, queued

from flycode.domain.orchestration import AccessScope
from flycode.domain.verification_control import (
    VerificationCommand,
    VerificationPlanDraft,
    VerificationProfileDraft,
)
from flycode.orchestration.host import Host
from flycode.orchestration.projects import Projects
from flycode.orchestration.store import StoreError
from flycode.verification.control import VerificationControl
from flycode.verification.registry import VerificationRegistry


def late_claim(store, tmp_path, *, assigned=True):
    source = tmp_path / 'late-project'
    source.mkdir()
    (source / 'test_answer.py').write_text('def test_answer(): assert True')
    project = Projects(store).register(source, actor_id='owner')
    owner = AccessScope(actor_id='owner', project_ids={project.id}, role='owner')
    registry = VerificationRegistry(store)
    subject = registry.register_subject(owner, project.id)
    profile = registry.register_profile(owner, project.id, VerificationProfileDraft(title='Late project tests'))
    plan = registry.create_plan(owner, VerificationPlanDraft(subject_ids={'target': subject.id},
        profile_id=profile.id, profile_version=1, budget={'max_tool_calls': 3, 'max_attempts': 1,
        'wall_seconds': 900.}, idempotency_key='late-project-plan'))
    execution = VerificationControl(store).command(owner, plan.id,
        VerificationCommand(operation='start', expected_revision=1, idempotency_key='late-project-start'))
    return claim(store, owner, execution) if assigned else execution


@pytest.mark.parametrize('assigned', [False, True])
def test_expiry_prior_scope_does_not_revoke_new_project_or_exit_coordinator(tmp_path, monkeypatch, assigned):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            claim(store, owner, first)
            prior_scope = host._scope(store)
            late = late_claim(store, tmp_path, assigned=assigned)
            store.clock = lambda: late.deadline_at
            failure = None
            try:
                VerificationControl(store).expire(prior_scope)
            except StoreError as error:
                failure = error.code
            assert failure is None, 'Metadata expiry crashed on a concurrently added project: ' + str(failure)
            assert VerificationControl(store)._execution(late.id) == late
            VerificationControl(store).expire(host._scope(store))
            assert VerificationControl(store)._execution(late.id).state == ('reconciling' if assigned else 'blocked')
        finally:
            store.close()


def test_host_drain_includes_claim_added_after_scope_read_before_writer_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    database, _, owner, _, first = queued(tmp_path, 'def test_answer(): assert True')
    with Host(database, tmp_path / 'host-state') as host:
        store = host.connect()
        try:
            original = claim(store, owner, first)
            connect = host.connect
            admitted = []
            def intercepted():
                connection = connect()
                transaction = connection.transaction
                @contextmanager
                def interleaved():
                    if not admitted:
                        admitted.append(late_claim(store, tmp_path))
                    with transaction():
                        yield
                connection.transaction = interleaved
                return connection
            with monkeypatch.context() as patch:
                patch.setattr(host, 'connect', intercepted)
                host.drain()
                assert admitted and host.session.phase == 'draining'
                for execution in (original, admitted[0]):
                    result = VerificationControl(store)._execution(execution.id)
                    assert result.state == 'reconciling' and result.fence > execution.fence
                    row = store.db.execute('SELECT uncertain FROM verification_reservations WHERE execution_id=?',
                        (execution.id,)).fetchone()
                    assert row[0] == 1
        finally:
            store.close()
