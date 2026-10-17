"""Independent registered review of immutable worker output; no approval booleans."""

import os
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from ..domain.results import ReviewExecution, ReviewRecord
from ..domain.verification import SubjectRef
from ..verification.custody import locked, stopped
from ..verification.engine import VerificationEngine
from ..verification.plan import PlanBuilder, contract_hash
from ..verification.snapshot import capture
from .artifacts import Artifacts
from .inputs import Inputs
from .store import Store, StoreError, changed
from .tools import Tools
from .verification import CheckExecutor, _succeed, accepted


class Reviews:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.artifacts = Artifacts(store, root)
        self.engine = VerificationEngine(self.root / 'verification')

    def get(self, scope, identity) -> ReviewRecord:
        record = self.store._load('reviews', identity, ReviewRecord)
        self.store.task(scope, self.store._attempt(record.reviewer_attempt_id).task_id)
        return record

    def run(self, scope, attempt_id, fence, artifact_id, *, approval_hash, reruns=0, trust='trusted-local-process') -> ReviewRecord:
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Review cannot run in a SQLite transaction')
        store._host_guard()
        if trust != 'trusted-local-process':
            raise StoreError('unsupported_sandbox')
        previous = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (attempt_id,)).fetchone()
        if previous:
            record = self.get(scope, previous['id'])
            if (record.reviewer_fence != fence or record.artifact_id != artifact_id
                    or record.approval_hash != approval_hash or record.reruns != reruns):
                raise StoreError('idempotency')
            return record
        if store.db.execute('SELECT 1 FROM review_executions WHERE id=?', (attempt_id,)).fetchone():
            store.task(scope, store._attempt(attempt_id).task_id)
            raise StoreError('reconcile_required', 'A recorded review execution cannot be replayed')
        attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        artifact = self.artifacts.get(scope, artifact_id)
        Inputs(store).require_artifact(scope, artifact)
        author = store._attempt(artifact.attempt_id)
        original = store.task(scope, artifact.task_id)
        if (task.kind != 'review' or attempt.profile_snapshot.role != 'reviewer' or attempt.instance_id == author.instance_id
                or task.workflow_id != artifact.workflow_id or task.input_artifacts.get(artifact_id) != artifact.patch_hash
                or task.acceptance is None or original.acceptance is None
                or contract_hash(task.acceptance) != contract_hash(original.acceptance)):
            raise StoreError('review_scope')
        if store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone():
            raise StoreError('worker_exists', 'A verification service cannot share a worker session')
        executor = CheckExecutor(store, scope, attempt_id, fence)
        engine = VerificationEngine(self.engine.data_dir, invocation_executor=executor.execute)
        approved = engine.oracles.read(approval_hash)
        directory = self.artifacts.directory / artifact_id
        roots = {'baseline': directory / 'baseline', **{stage: directory / 'target' for stage in ('target', 'regression', 'integration')}}
        ref = SubjectRef(project_id=task.project_id, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id)
        snapshots = {stage: capture(root, ref=ref) for stage, root in roots.items()}
        plan = PlanBuilder().build(snapshots['integration'], task.acceptance, approved.checks,
            subjects=snapshots, approval_hash=approval_hash, reruns=reruns)
        directory = self.root / 'reviews' / attempt_id
        directory.mkdir(parents=True, mode=0o700)
        with locked(directory / 'coordinator.lock', create=True) as descriptor:
            info = os.fstat(descriptor)
            execution = ReviewExecution(attempt_id=attempt_id, project_id=task.project_id, workflow_id=workflow.id,
                task_id=task.id, fence=fence, artifact_id=artifact_id, patch_hash=artifact.patch_hash,
                approval_hash=approval_hash, reruns=reruns, runtime_root=str(self.root),
                coordinator_identity=(info.st_dev, info.st_ino), created_at=store.clock())
            with store.transaction():
                store._claim_execution(scope, attempt_id, fence, 'review')
                store.db.execute('INSERT INTO review_executions VALUES (?,?,?)',
                    (attempt_id, execution.state, execution.model_dump_json()))
            return self._execute(scope, attempt, task, workflow, artifact, approval_hash, reruns, engine, executor, plan, roots)

    def _execute(self, scope, attempt, task, workflow, artifact, approval_hash, reruns, engine, executor, plan, roots):
        store, attempt_id, fence, artifact_id = self.store, attempt.id, attempt.fence, artifact.id
        verdict = engine.verify_plan(plan, roots, should_stop=executor.should_stop)
        self.artifacts.get(scope, artifact_id)
        passed = accepted(engine, verdict, roots['integration'], task.acceptance)
        stop_reason = None
        authority_lost = store._host_late()
        with store.transaction():
            store.task(scope, task.id)
            try:
                if authority_lost or store._host_late():
                    raise StoreError('host_fence')
                current_attempt, current_task, current_workflow = store._owned(scope, attempt_id, fence, running=True)
                if current_task.paused or current_workflow.state != 'running' or store._counter('queue_paused'):
                    raise StoreError('paused')
            except StoreError as error:
                if error.code not in {'fence', 'paused', 'wall_deadline', 'host_fence'}:
                    raise
                passed, stop_reason = False, error.code
            record = ReviewRecord(id=uuid4().hex, artifact_id=artifact_id, patch_hash=artifact.patch_hash,
                reviewer_attempt_id=attempt_id, reviewer_fence=fence, reviewer_contract_hash=task.contract_hash,
                approval_hash=approval_hash, reruns=reruns, status='approved' if passed else 'inconclusive' if stop_reason else
                    'changes_requested' if verdict.status == 'failed' else 'inconclusive', verdict=verdict, created_at=store.clock())
            store.db.execute('INSERT INTO reviews VALUES (?,?,?,?)', (record.id, artifact_id, attempt_id, record.model_dump_json()))
            execution = changed(self.execution(scope, attempt_id), state='completed', review_id=record.id)
            store.db.execute('UPDATE review_executions SET state=?,data=? WHERE id=?',
                (execution.state, execution.model_dump_json(), attempt_id))
            if passed:
                _succeed(store, scope, current_attempt, current_task, current_workflow, evidence_id=verdict.evidence_id)
            store._event(scope, workflow, 'review_completed', task=task, attempt=attempt, review_id=record.id,
                artifact_id=artifact.id, patch_hash=artifact.patch_hash, status=record.status,
                evidence_id=verdict.evidence_id, stop_reason=stop_reason)
        if not passed:
            try:
                current_task = store.task(scope, task.id)
                current_workflow = store.workflow(scope, workflow.id)
                outcome = 'retry_wait' if current_task.paused or current_workflow.state == 'pausing' else 'failed'
                store.confirm_stopped(scope, attempt_id, fence, outcome=outcome, expenditure_confirmed=True)
            except StoreError as error:
                if error.code not in {'resource_busy', 'uncertain'}:
                    raise
        return record


    def execution(self, scope, attempt_id) -> ReviewExecution:
        self.store.task(scope, self.store._attempt(attempt_id).task_id)
        if (not self.store.db.execute('SELECT 1 FROM review_executions WHERE id=?', (attempt_id,)).fetchone()
                and self.store.db.execute("SELECT 1 FROM execution_lanes WHERE attempt_id=? AND mode='review'", (attempt_id,)).fetchone()):
            raise StoreError('ownership_unknown', 'Legacy review has no durable coordinator custody')
        return self.store._load('review_executions', attempt_id, ReviewExecution)

    def _save_execution(self, scope, record, **updates):
        record = changed(record, **updates)
        with self.store.transaction():
            self.store.task(scope, record.task_id)
            self.store.db.execute('UPDATE review_executions SET state=?,data=? WHERE id=?',
                (record.state, record.model_dump_json(), record.attempt_id))
        return record

    def reconcile(self, scope, attempt_id) -> ReviewExecution:
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        if store.db.in_transaction:
            raise RuntimeError('Review reconciliation cannot run in a SQLite transaction')
        record = self.execution(scope, attempt_id)
        if record.state == 'completed' or (record.state == 'interrupted' and store._attempt(attempt_id).stop_confirmed):
            return record
        if record.runtime_root != str(self.root):
            raise StoreError('ownership_unknown')
        try:
            with locked(self.root / 'reviews' / attempt_id / 'coordinator.lock', identity=record.coordinator_identity):
                return self._recover(scope, self.execution(scope, attempt_id))
        except BlockingIOError as error:
            raise StoreError('coordinator_running') from error
        except (OSError, ValueError) as error:
            if isinstance(error, StoreError):
                raise
            raise StoreError('ownership_unknown') from error

    def _recover(self, scope, record):
        store = self.store
        with store.transaction():
            attempt = store._attempt(record.attempt_id)
            if record.state == 'completed' or (record.state == 'interrupted' and attempt.stop_confirmed):
                return record
            task, workflow = store.task(scope, record.task_id), store.workflow(scope, record.workflow_id)
            if attempt.fence != record.fence or task.current_attempt_id != attempt.id:
                raise StoreError('fence')
            if attempt.state in {'assigned', 'running', 'stopping'}:
                attempt = store._reconcile(scope, workflow, attempt, 'review_recovery')
            record = changed(record, state='reconciling', error='host_interrupted')
            store.db.execute('UPDATE review_executions SET state=?,data=? WHERE id=?',
                (record.state, record.model_dump_json(), attempt.id))
        tools = Tools(store)
        with ExitStack() as locks:
            for call in tools.calls(scope, workflow.id):
                if call.attempt_id != attempt.id:
                    continue
                if call.process is not None:
                    try:
                        receipt = locks.enter_context(stopped(call.process))
                    except (OSError, ValueError):
                        return self._save_execution(scope, record, error='process_unconfirmed')
                    if not receipt.started and call.execution_profile == 'registered-pytest-v1' and call.status in {'running', 'uncertain'}:
                        tools.finish(scope, call.id, status='failed', error_class='NeverStarted')
                elif call.execution_profile == 'registered-pytest-v1' and call.status in {'running', 'uncertain'}:
                    tools.finish(scope, call.id, status='failed', error_class='NeverAdmitted')
            unknown = any(call.attempt_id == attempt.id and call.status in {'running', 'uncertain'}
                for call in tools.calls(scope, workflow.id))
            unknown |= store.db.execute("SELECT 1 FROM model_calls WHERE attempt_id=? AND state IN ('running','uncertain')",
                (attempt.id,)).fetchone() is not None
            if unknown:
                return self._save_execution(scope, record, error='effects_unknown')
            with store.transaction():
                record = changed(record, state='interrupted', error='host_interrupted')
                store.db.execute('UPDATE review_executions SET state=?,data=? WHERE id=?',
                    (record.state, record.model_dump_json(), attempt.id))
                store._confirm_stopped(scope, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
                store._event(scope, workflow, 'review_reconciled', task=task, attempt=attempt)
            return record
