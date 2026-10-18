"""Fenced registered pytest execution under the canonical database Host."""

import os
import stat
from contextlib import ExitStack
from pathlib import Path

from ..domain.orchestration import AccessScope
from ..domain.verification import CheckAttempt
from ..domain.verification_control import (
    RegisteredVerificationPlan,
    VerificationInvocation,
)
from ..orchestration.sources import Sources
from ..orchestration.store import StoreError, changed
from .control import TERMINAL, VerificationControl
from .custody import locked, stopped
from .engine import VerificationEngine
from .oracle import OracleRegistry
from .registry import VerificationRegistry
from .snapshot import capture, digest


class VerificationExecutor:
    def __init__(self, store, execution_id, fence):
        self.store, self.identity, self.fence = store, execution_id, fence
        self.control = VerificationControl(store)
        execution = self.control._execution(execution_id)
        record = store._load('verification_plans', execution.plan_id, RegisteredVerificationPlan)
        self.scope = AccessScope(actor_id='managed-verification', project_ids={record.project_id}, role='runtime')
        self.pulse = store.clock()
        self.descriptor = None

    def _custody(self, execution):
        try:
            if execution.coordinator_root is None or execution.coordinator_identity is None or self.descriptor is None:
                raise StoreError('ownership_unknown')
            root = Path(execution.coordinator_root.path)
            VerificationRegistry(self.store)._stage_current(root, execution.coordinator_root)
            for info in (os.fstat(self.descriptor), (root / 'coordinator.lock').lstat()):
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or (info.st_dev, info.st_ino) != execution.coordinator_identity):
                    raise StoreError('ownership_unknown')
        except (OSError, ValueError) as error:
            raise StoreError('ownership_unknown') from error

    def should_stop(self):
        try:
            self.store._host_guard()
            execution, _ = self.control._owned(self.scope, self.identity, self.fence)
            self._custody(execution)
            if self.store.clock() - self.pulse >= 10:
                self.control.heartbeat(self.scope, self.identity, self.fence)
                self.pulse = self.store.clock()
            return False
        except StoreError:
            return True

    def _replace_call(self, execution, call):
        return changed(execution, invocations=tuple(call if old.id == call.id else old for old in execution.invocations))

    def _call(self, execution, identity):
        return next(call for call in execution.invocations if call.id == identity)

    def _begin(self, check, number):
        store, control = self.store, self.control
        store._host_guard()
        with store.transaction():
            execution, record = control._owned(self.scope, self.identity, self.fence)
            if (check not in record.plan.checks or not 1 <= number <= record.plan.reruns + 1
                    or any(call.check_id == check.id and call.number == number for call in execution.invocations)):
                raise StoreError('request_replay')
            if any(call.state != 'completed' for call in execution.invocations):
                raise StoreError('resource_busy')
            ledger = store.db.execute('SELECT * FROM verification_reservations WHERE execution_id=?', (execution.id,)).fetchone()
            if ledger['uncertain'] or ledger['tool_reserved'] < 1:
                raise StoreError('budget')
            call = VerificationInvocation(check_id=check.id, number=number, fence=self.fence,
                timeout=min(check.timeout, execution.deadline_at - store.clock()))
            store.db.execute('UPDATE verification_reservations SET tool_reserved=tool_reserved-1,tool_spent=tool_spent+1 '
                'WHERE execution_id=?', (execution.id,))
            execution = changed(execution, invocations=(*execution.invocations, call))
            control._save_execution(execution)
            control._event(self.scope, record, 'check_reserved', execution)
            return call

    def _process(self, identity, ref, *, admit):
        store, control = self.store, self.control
        store._host_guard()
        execution, record = control._owned(self.scope, self.identity, self.fence)
        project, profile = control._preflight(self.scope, record, owner=False)
        if execution.coordinator_root is None:
            raise StoreError('ownership_unknown')
        root = Path(execution.coordinator_root.path)
        self._custody(execution)
        if ref.version != 'pytest-custody-v1' or not Path(ref.directory).resolve().is_relative_to(root / 'evidence'):
            raise StoreError('process_admission')
        with store.transaction():
            execution, record = control._owned(self.scope, self.identity, self.fence)
            control._bindings_sql(self.scope, record, project, profile)
            call = self._call(execution, identity)
            if call.state != 'preparing' or (call.process is not None and call.process != ref):
                raise StoreError('process_admission')
            if admit and call.process != ref:
                raise StoreError('process_admission')
            call = changed(call, process=ref, state='running' if admit else 'preparing')
            execution = self._replace_call(execution, call)
            if admit:
                execution = changed(execution, state='running')
                record = changed(record, state='running', revision=record.revision + 1)
                control._save(record)
            control._save_execution(execution)
            control._event(self.scope, record, 'check_admitted' if admit else 'check_prepared', execution)

    def _finish(self, identity, result):
        store, control = self.store, self.control
        execution = control._execution(self.identity)
        call = self._call(execution, identity)
        confirmed = call.process is None and result is not None and not result.process_started
        with ExitStack() as custody:
            if call.process is not None:
                try:
                    custody.enter_context(stopped(call.process))
                    confirmed = True
                except (OSError, ValueError):
                    confirmed = False
            late = store._host_late()
            with store.transaction():
                execution = control._execution(self.identity)
                call = self._call(execution, identity)
                try:
                    control._owned(self.scope, self.identity, self.fence)
                except StoreError:
                    late = True
                if call.fence != self.fence or call.state == 'completed':
                    raise StoreError('fence')
                call = changed(call, state='completed' if confirmed else 'uncertain', result=result, late=late)
                control._save_execution(self._replace_call(execution, call))
                if not confirmed:
                    store.db.execute('UPDATE verification_reservations SET uncertain=1 WHERE execution_id=?', (self.identity,))
                record = control._record(self.scope, execution.plan_id)
                control._event(self.scope, record, 'check_stopped' if confirmed else 'check_uncertain', execution)

    def execute(self, check, number, invocation):
        try:
            call = self._begin(check, number)
        except StoreError as error:
            return CheckAttempt(number=number, argv=[], exit_code=None, duration=0, state='cancelled', cancelled=True,
                error_class='policy', reasons=[error.code])
        try:
            result = invocation(timeout=call.timeout,
                on_prepare=lambda ref: self._process(call.id, ref, admit=False),
                on_admit=lambda ref: self._process(call.id, ref, admit=True))
            result = changed(result, check_id=check.id, plan_id=self.control._execution(self.identity).plan_id,
                subject_hash=check.subject_hash)
        except BaseException:
            self._finish(call.id, None)
            raise
        self._finish(call.id, result)
        return result

    def _current_source(self, registry, record, project):
        stage = 'integration' if 'integration' in record.subject_ids else 'target'
        registered = registry.subject(self.scope, record.subject_ids[stage])
        snapshot = registered.snapshot
        current = capture(Path(project.root.path))
        base = registered.origin.source_base if registered.origin is not None else snapshot.base
        return (current.files == snapshot.files and current.modes == snapshot.modes and current.base == base
            and digest(current.environment) == digest(snapshot.environment))

    def _settle(self, verdict, *, engine, root):
        store, control, registry = self.store, self.control, VerificationRegistry(self.store)
        execution = control._execution(self.identity)
        if execution.fence != self.fence or execution.state in {'completed', 'cancelled', 'interrupted', 'blocked'}:
            return execution
        current, reason, prepared = False, None, None
        try:
            record = control._record(self.scope, execution.plan_id)
            project, profile = control._preflight(self.scope, record, owner=False)
            prepared = project, profile
            current = engine.current(verdict, root, contract=record.plan.acceptance, captured=True)
            if not current:
                reason = 'evidence_changed'
            source_current = self._current_source(registry, record, project)
            current &= source_current
            registry._unchanged(project, registry._storage(project))
            if not source_current:
                reason = 'subject_changed'
        except (OSError, ValueError) as error:
            current, reason = False, getattr(error, 'code', 'inputs_unavailable')
        late = store._host_late()
        with ExitStack() as custody:
            try:
                self._custody(execution)
                for call in execution.invocations:
                    if call.state != 'completed':
                        raise StoreError('uncertain')
                    if call.process is not None:
                        custody.enter_context(stopped(call.process))
            except (OSError, ValueError):
                return self._interrupt('ownership_unknown')
            with store.transaction():
                execution = control._execution(self.identity)
                record = control._record(self.scope, execution.plan_id)
                if execution.fence != self.fence:
                    return execution
                try:
                    control._owned(self.scope, self.identity, self.fence)
                except StoreError as error:
                    late, reason = True, error.code
                if not late and prepared is not None:
                    try:
                        control._bindings_sql(self.scope, record, *prepared)
                    except StoreError as error:
                        current, reason = False, error.code
                cancelled = execution.cancel_requested or execution.state == 'cancelling' or verdict.status == 'cancelled'
                execution = changed(execution, state='cancelled' if cancelled else 'interrupted' if late else 'completed',
                    verdict=verdict, current=current and not late and not cancelled, error=reason,
                    ended_at=store.clock(), lease_until=None)
                control._save_execution(execution)
                store.db.execute('UPDATE verification_reservations SET tool_reserved=0,uncertain=0 WHERE execution_id=?',
                    (execution.id,))
                record = changed(record, state=execution.state, revision=record.revision + 1)
                control._save(record)
                control._event(self.scope, record, 'execution_' + execution.state, execution)
                return execution

    def _interrupt(self, reason):
        with self.store.transaction():
            execution = self.control._execution(self.identity)
            if execution.fence != self.fence or execution.state in {'completed', 'cancelled', 'interrupted', 'blocked'}:
                return execution
            execution = changed(execution, state='reconciling', current=False, error=reason,
                cancel_requested=execution.cancel_requested or execution.state == 'cancelling')
            self.control._save_execution(execution)
            self.store.db.execute('UPDATE verification_reservations SET uncertain=1 WHERE execution_id=?', (self.identity,))
            record = self.control._record(self.scope, execution.plan_id)
            record = changed(record, state='reconciling', revision=record.revision + 1)
            self.control._save(record)
            self.control._event(self.scope, record, 'execution_reconciling', execution)
            return execution

    def recover(self):
        store, control = self.store, self.control
        store._host_guard()
        if store.host is None:
            raise StoreError('host_required')
        execution = control._execution(self.identity)
        control._record(self.scope, execution.plan_id)
        if execution.fence != self.fence:
            raise StoreError('fence')
        if execution.state in TERMINAL:
            return execution
        if execution.state != 'reconciling':
            raise StoreError('verification_state')
        with ExitStack() as custody:
            if execution.coordinator_root is not None:
                root = Path(execution.coordinator_root.path)
                try:
                    VerificationRegistry(store)._stage_current(root, execution.coordinator_root)
                    if execution.coordinator_identity is None:
                        raise StoreError('ownership_unknown')
                    descriptor = custody.enter_context(locked(root / 'coordinator.lock', identity=execution.coordinator_identity))
                except BlockingIOError as error:
                    raise StoreError('coordinator_running') from error
                except (OSError, ValueError) as error:
                    raise StoreError('ownership_unknown') from error
                self.descriptor = descriptor
                custody.callback(setattr, self, 'descriptor', None)
                self._custody(execution)
            elif execution.coordinator_identity is not None or execution.invocations:
                raise StoreError('ownership_unknown')
            for call in execution.invocations:
                if call.process is not None:
                    try:
                        custody.enter_context(stopped(call.process))
                    except (OSError, ValueError) as error:
                        raise StoreError('process_unconfirmed') from error
                elif call.state == 'running' or (call.result is not None and call.result.process_started):
                    raise StoreError('ownership_unknown')
            if execution.coordinator_root is not None:
                self._custody(execution)
            store._host_guard()
            with store.transaction():
                store._host_sql()
                current = control._execution(self.identity)
                if current != execution:
                    raise StoreError('revision')
                calls = tuple(changed(call, state='completed', late=True) for call in execution.invocations)
                execution = changed(execution, state='cancelled' if execution.cancel_requested else 'interrupted',
                    invocations=calls, current=False, ended_at=store.clock(), lease_until=None)
                control._save_execution(execution)
                store.db.execute('UPDATE verification_reservations SET tool_reserved=0,uncertain=0 WHERE execution_id=?',
                    (execution.id,))
                record = control._record(self.scope, execution.plan_id)
                record = changed(record, state=execution.state, revision=record.revision + 1)
                control._save(record)
                control._event(self.scope, record, 'execution_reconciled', execution)
                return execution

    def run(self):
        store, control, registry = self.store, self.control, VerificationRegistry(self.store)
        if self.descriptor is not None:
            raise StoreError('execution_busy')
        conflict, acquired = False, False
        try:
            store._host_guard()
            execution, record = control._owned(self.scope, self.identity, self.fence)
            if execution.coordinator_identity is not None:
                conflict = True
                raise StoreError('execution_busy')
            project, profile = control._preflight(self.scope, record, owner=False)
            storage = registry._storage(project)
            directory = registry._directory(storage, 'executions', execution.id, create=True)
            root = Sources(store).prepare(directory)
            with ExitStack() as custody:
                try:
                    descriptor = custody.enter_context(locked(directory / 'coordinator.lock', create=True))
                except (FileExistsError, BlockingIOError):
                    conflict = True
                    raise
                acquired = True
                self.descriptor = descriptor
                info = os.fstat(descriptor)
                with store.transaction():
                    execution, record = control._owned(self.scope, self.identity, self.fence)
                    execution = changed(execution, coordinator_root=root, coordinator_identity=(info.st_dev, info.st_ino))
                    control._save_execution(execution)
                engine = VerificationEngine(directory / 'evidence', invocation_executor=self.execute)
                if profile.approval_hash is not None:
                    engine.oracles = OracleRegistry(registry._directory(storage, 'oracles', project.id, profile.id))
                roots = {stage: registry.subject_root(self.scope, identity) for stage, identity in record.subject_ids.items()}
                verdict = engine.verify_plan(record.plan, roots, should_stop=self.should_stop, captured=True)
                return self._settle(verdict, engine=engine, root=roots['integration' if 'integration' in roots else 'target'])
        except BaseException as error:
            if not conflict:
                self._interrupt(getattr(error, 'code', type(error).__name__))
            raise
        finally:
            if acquired:
                self.descriptor = None
