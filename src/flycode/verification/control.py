"""Transactional verification intent; execution is owned by the shared Host."""
import json

from ..domain.verification_control import (
    RegisteredSubject,
    RegisteredVerificationPlan,
    VerificationCommand,
    VerificationEvent,
    VerificationExecution,
)
from ..orchestration.projects import Projects
from ..orchestration.store import StoreError, changed
from .registry import VerificationRegistry
from .snapshot import digest, environment_identity

ACTIVE = {'queued', 'preparing', 'running', 'evaluating', 'cancelling', 'reconciling'}
TERMINAL = {'completed', 'blocked', 'cancelled', 'interrupted'}


class VerificationControl:
    def __init__(self, store):
        self.store = store

    def _record(self, scope, identity, *, owner=False):
        record = self.store._load('verification_plans', identity, RegisteredVerificationPlan)
        self.store._scope(scope, record.project_id, ('owner',) if owner else ())
        return record

    def get(self, scope, identity) -> RegisteredVerificationPlan:
        record = self._record(scope, identity)
        project = VerificationRegistry(self.store)._project(scope, record.project_id)
        if project.revision != record.project_revision:
            raise StoreError('project_changed')
        return record

    def events(self, scope, identity, *, after=0, limit=100) -> list[VerificationEvent]:
        self.get(scope, identity)
        if not 0 <= after <= 2**63 - 1 or not 1 <= limit <= 100:
            raise StoreError('cursor')
        return [VerificationEvent(sequence=row['sequence'], **json.loads(row['data'])) for row in self.store.db.execute(
            'SELECT sequence,data FROM verification_events WHERE plan_id=? AND sequence>? ORDER BY sequence LIMIT ?',
            (identity, after, limit))]

    def _event(self, scope, record, kind, execution=None):
        data = {'project_id': record.project_id, 'plan_id': record.id, 'actor_id': scope.actor_id, 'kind': kind,
            'occurred_at': self.store.clock(), 'revision': record.revision,
            'execution_id': execution.id if execution else None}
        self.store.db.execute('INSERT INTO verification_events(project_id,plan_id,data) VALUES (?,?,?)',
            (record.project_id, record.id, json.dumps(data, allow_nan=False)))

    def _save(self, record):
        self.store.db.execute('UPDATE verification_plans SET data=? WHERE id=?', (record.model_dump_json(), record.id))

    def _execution(self, identity):
        return self.store._load('verification_executions', identity, VerificationExecution)

    def _save_execution(self, execution):
        self.store.db.execute('UPDATE verification_executions SET state=?,data=? WHERE id=?',
            (execution.state, execution.model_dump_json(), execution.id))

    def _preflight(self, scope, record, *, owner=True):
        registry = VerificationRegistry(self.store)
        project = registry._project(scope, record.project_id, owner=owner)
        profile = registry.profile(scope, record.profile_id)
        if profile.version != record.profile_version or project.revision != record.project_revision:
            raise StoreError('verification_profile_changed')
        if record.plan.trust != 'trusted-local-process':
            raise StoreError('sandbox_unavailable')
        if digest(record.plan.model_dump(mode='json')) != record.plan_hash:
            raise StoreError('verification_plan_changed')
        environment = digest(environment_identity())
        for stage, identity in record.subject_ids.items():
            subject = registry.subject(scope, identity)
            registry.subject_root(scope, identity)
            registry._subject_origin(scope, subject)
            if (subject.project_id != record.project_id or subject.snapshot.model_dump(mode='json') !=
                    record.plan.subjects[stage].model_dump(mode='json')):
                raise StoreError('verification_subject_changed')
            if digest(subject.snapshot.environment) != environment:
                raise StoreError('verification_environment_changed')
        return project, profile

    def _admissible(self, scope, record):
        if self.store._counter('queue_paused'):
            raise StoreError('paused')
        project = Projects(self.store).get(record.project_id)
        if project.state != 'active' or project.revision != record.project_revision:
            raise StoreError('project_changed')
        if record.workflow_id is not None:
            workflow = self.store.workflow(scope, record.workflow_id)
            task = self.store.task(scope, record.task_id)
            if (task.workflow_id != workflow.id or task.contract_hash != record.task_contract_hash
                    or workflow.semantic_revision != record.workflow_semantic_revision):
                raise StoreError('contract_changed')
            if workflow.state != 'running' or task.paused:
                raise StoreError('paused')

    def _bindings_sql(self, scope, record, project, profile):
        registry = VerificationRegistry(self.store)
        registry._project_sql(project)
        registry._profile_sql(profile)
        for identity in record.subject_ids.values():
            subject = self.store._load('verification_subjects', identity, RegisteredSubject)
            if subject.origin is not None:
                registry._origin_sql(scope, subject.origin)

    def claim(self, scope, identity) -> VerificationExecution:
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        if store.host is None:
            raise StoreError('host_required')
        execution = self._execution(identity)
        prepared = self._record(scope, execution.plan_id)
        project, profile = self._preflight(scope, prepared, owner=False)
        with store.transaction():
            store._host_sql()
            execution = self._execution(identity)
            record = self._record(scope, execution.plan_id)
            if execution.state != 'queued' or record.state != 'queued' or record.current_execution_id != identity:
                raise StoreError('verification_state')
            if record != prepared:
                raise StoreError('revision')
            self._admissible(scope, record)
            self._bindings_sql(scope, record, project, profile)
            now = store.clock()
            if now >= execution.deadline_at or now < execution.created_at:
                raise StoreError('deadline')
            if store._active_execution_count() >= 4 or (record.workflow_id is not None and
                    store._active_execution_count(record.workflow_id) >= store.workflow(scope, record.workflow_id).budget.max_instances):
                raise StoreError('instance_limit')
            execution = changed(execution, state='preparing', fence=execution.fence + 1,
                started_at=now, lease_until=now + 30.,
                host_session_id=store.host.session.id, host_generation=store.host.session.generation)
            self._save_execution(execution)
            record = changed(record, state='preparing', revision=record.revision + 1)
            self._save(record)
            self._event(scope, record, 'execution_claimed', execution)
            return execution

    def _owned(self, scope, identity, fence):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_sql()
        execution = self._execution(identity)
        record = self._record(scope, execution.plan_id)
        if (store.host is None or execution.host_session_id != store.host.session.id
                or execution.host_generation != store.host.session.generation or execution.fence != fence
                or execution.state not in {'preparing', 'running', 'evaluating'}
                or any(call.late for call in execution.invocations)
                or record.current_execution_id != execution.id or execution.lease_until is None
                or store.clock() >= execution.lease_until):
            raise StoreError('fence')
        if (store.clock() >= execution.deadline_at or execution.started_at is None
                or store.clock() < execution.started_at):
            raise StoreError('deadline')
        self._admissible(scope, record)
        return execution, record

    def heartbeat(self, scope, identity, fence):
        self.store._host_guard()
        with self.store.transaction():
            execution, _ = self._owned(scope, identity, fence)
            execution = changed(execution, lease_until=self.store.clock() + 30.)
            self._save_execution(execution)
            return execution

    def _revoke_all(self, scope, reason):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_sql()
        if store.host is None or not store.db.in_transaction:
            raise StoreError('host_required')
        for row in store.db.execute("SELECT id FROM verification_executions WHERE state IN "
                "('preparing','running','evaluating','cancelling','reconciling')").fetchall():
            execution = self._execution(row['id'])
            if execution.state == 'reconciling' and execution.host_session_id == store.host.session.id:
                continue
            self._revoke(scope, execution, reason)

    def _revoke(self, scope, execution, reason):
        store = self.store
        record = self._record(scope, execution.plan_id)
        execution = changed(execution, state='reconciling', fence=execution.fence + 1, lease_until=None,
            current=False, error=reason, cancel_requested=execution.cancel_requested or execution.state == 'cancelling',
            host_session_id=store.host.session.id, host_generation=store.host.session.generation)
        self._save_execution(execution)
        store.db.execute('UPDATE verification_reservations SET uncertain=1 WHERE execution_id=?', (execution.id,))
        record = changed(record, state='reconciling', revision=record.revision + 1)
        self._save(record)
        self._event(scope, record, 'execution_revoked', execution)

    def expire(self, scope):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        with store.transaction():
            store._host_sql()
            if store.host is None:
                raise StoreError('host_required')
            for row in store.db.execute("SELECT e.id,p.project_id FROM verification_executions e "
                    "JOIN verification_plans p ON p.id=e.plan_id WHERE e.state='queued'").fetchall():
                if row['project_id'] not in scope.project_ids:
                    continue
                execution = self._execution(row['id'])
                if store.clock() >= execution.deadline_at or store.clock() < execution.created_at:
                    self._block_queued(scope, execution, 'deadline')
            for row in store.db.execute("SELECT e.id,p.project_id FROM verification_executions e "
                    "JOIN verification_plans p ON p.id=e.plan_id WHERE e.state IN "
                    "('preparing','running','evaluating','cancelling')").fetchall():
                if row['project_id'] not in scope.project_ids:
                    continue
                execution = self._execution(row['id'])
                try:
                    self._owned(scope, execution.id, execution.fence)
                except StoreError as error:
                    self._revoke(scope, execution, error.code)

    def _block_queued(self, scope, execution, reason):
        record = self._record(scope, execution.plan_id)
        if (execution.state != 'queued' or execution.invocations or execution.host_session_id is not None
                or record.current_execution_id != execution.id):
            raise StoreError('verification_state')
        execution = changed(execution, state='blocked', current=False, error=reason, ended_at=self.store.clock())
        self._save_execution(execution)
        self.store.db.execute('UPDATE verification_reservations SET tool_reserved=0 WHERE execution_id=?', (execution.id,))
        record = changed(record, state='blocked', revision=record.revision + 1)
        self._save(record)
        self._event(scope, record, 'execution_blocked', execution)
        return execution

    def block_queued(self, scope, identity, reason):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        with store.transaction():
            store._host_sql()
            if store.host is None:
                raise StoreError('host_required')
            execution = self._execution(identity)
            self._record(scope, execution.plan_id)
            if execution.state != 'queued':
                return execution
            return self._block_queued(scope, execution, reason)

    def recovery_blocked(self, scope, identity, fence, reason):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        with store.transaction():
            store._host_sql()
            if store.host is None:
                raise StoreError('host_required')
            execution = self._execution(identity)
            record = self._record(scope, execution.plan_id)
            if execution.state != 'reconciling' or execution.fence != fence or execution.error == reason:
                return execution
            execution = changed(execution, error=reason)
            self._save_execution(execution)
            record = changed(record, revision=record.revision + 1)
            self._save(record)
            self._event(scope, record, 'reconciliation_blocked', execution)
            return execution

    def command(self, scope, identity, command: VerificationCommand):
        command = VerificationCommand.model_validate(command.model_dump())
        record = self._record(scope, identity, owner=True)
        request = {'verification_command': identity, **command.model_dump(mode='json')}
        # A replay is inert and returns the original receipt, even when later
        # commands changed the current revision. It never starts fresh work.
        found, result, _ = self.store._replay(scope, command.idempotency_key, request)
        if found:
            return self._response(result)
        prepared = self._preflight(scope, record) if command.operation != 'cancel' else None
        with self.store.transaction():
            found, result, request_hash = self.store._replay(scope, command.idempotency_key, request)
            if found:
                return self._response(result)
            record = self._record(scope, identity, owner=True)
            if record.revision != command.expected_revision:
                raise StoreError('revision', current=record)
            if command.operation == 'cancel':
                result = self._cancel(scope, record)
            else:
                assert prepared is not None
                project, profile = prepared
                if Projects(self.store).get(project.id) != project:
                    raise StoreError('project_changed')
                VerificationRegistry(self.store)._profile_sql(profile)
                result = self._queue(scope, record, command.operation)
            self.store._remember(scope, command.idempotency_key, record.project_id, request_hash, result)
            return result

    def _response(self, result):
        return VerificationExecution.model_validate(result) if 'plan_id' in result else RegisteredVerificationPlan.model_validate(result)

    def _queue(self, scope, record, operation):
        if operation == 'start' and record.state != 'draft':
            raise StoreError('verification_state')
        if operation == 'rerun' and (record.state not in TERMINAL or record.current_execution_id is None):
            raise StoreError('verification_state')
        rows = [VerificationExecution.model_validate_json(row['data']) for row in self.store.db.execute(
            'SELECT data FROM verification_executions WHERE plan_id=?', (record.id,))]
        if any(row.state in ACTIVE for row in rows):
            raise StoreError('resource_busy')
        number = len(rows) + 1
        if number > record.budget.max_attempts:
            raise StoreError('attempt_limit')
        ledger = self.store.db.execute('''SELECT COALESCE(SUM(tool_reserved),0) AS reserved,
            COALESCE(SUM(tool_spent),0) AS spent,COALESCE(MAX(uncertain),0) AS uncertain
            FROM verification_reservations JOIN verification_executions ON id=execution_id WHERE plan_id=?''',
            (record.id,)).fetchone()
        if ledger['uncertain']:
            raise StoreError('resource_busy')
        needed = len(record.plan.checks) * (record.plan.reruns + 1)
        if ledger['spent'] + ledger['reserved'] + needed > record.budget.max_tool_calls:
            raise StoreError('budget')
        now = self.store.clock()
        deadline = record.deadline_at if record.deadline_at is not None else now + record.budget.wall_seconds
        if record.workflow_id is not None:
            workflow = self.store.workflow(scope, record.workflow_id)
            task = self.store.task(scope, record.task_id)
            if (task.workflow_id != workflow.id or task.contract_hash != record.task_contract_hash
                    or workflow.semantic_revision != record.workflow_semantic_revision):
                raise StoreError('contract_changed')
            if workflow.state != 'running' or task.paused or self.store._counter('queue_paused'):
                raise StoreError('paused')
            if workflow.deadline_at is None:
                raise StoreError('workflow_not_started')
            shared = self.store.budget(scope, workflow.id)
            if shared.uncertain or shared.free.tool_calls < needed:
                raise StoreError('budget')
            deadline = min(deadline, workflow.deadline_at)
        if now >= deadline or any(now < max(row.created_at, row.started_at or row.created_at) for row in rows):
            raise StoreError('deadline')
        execution = VerificationExecution(plan_id=record.id, number=number, created_by=scope.actor_id,
            created_at=now, deadline_at=deadline)
        self.store.db.execute('INSERT INTO verification_executions VALUES (?,?,?,?,?)',
            (execution.id, record.id, number, execution.state, execution.model_dump_json()))
        self.store.db.execute('INSERT INTO verification_reservations VALUES (?,?,?,0,0)',
            (execution.id, record.workflow_id, needed))
        record = changed(record, state='queued', revision=record.revision + 1,
            current_execution_id=execution.id, deadline_at=deadline)
        self._save(record)
        self._event(scope, record, 'execution_queued', execution)
        return execution

    def _cancel(self, scope, record):
        if record.state == 'draft':
            record = changed(record, state='cancelled', revision=record.revision + 1)
            self._save(record)
            self._event(scope, record, 'plan_cancelled')
            return record
        if record.current_execution_id is None:
            raise StoreError('verification_state')
        execution = self._execution(record.current_execution_id)
        if execution.state in {'cancelled', 'cancelling'}:
            return execution
        if execution.state not in ACTIVE:
            raise StoreError('verification_state')
        queued = execution.state == 'queued'
        execution = changed(execution, state='cancelled' if queued else 'cancelling',
            cancel_requested=True, ended_at=self.store.clock() if queued else None)
        self._save_execution(execution)
        if queued:
            self.store.db.execute('UPDATE verification_reservations SET tool_reserved=0 WHERE execution_id=?', (execution.id,))
        record = changed(record, state=execution.state, revision=record.revision + 1)
        self._save(record)
        self._event(scope, record, 'execution_cancelled' if queued else 'execution_cancelling', execution)
        return execution
