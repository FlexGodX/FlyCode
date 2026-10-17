"""Host-approved source bindings and baseline dispatch through the canonical store."""

from pathlib import Path
from subprocess import SubprocessError
from uuid import uuid4

from ..brain import BRAIN_REGISTRY
from ..domain.orchestration import (
    ClaimResult,
    IntegrationBinding,
    ReviewBinding,
    WorkerBinding,
    stage_binding,
)
from ..verification.snapshot import capture
from .integration import Integration
from .integration_bindings import IntegrationBindings
from .review import Reviews
from .review_bindings import ReviewBindings
from .runtime import Runtime, WorkerHandle
from .store import Store, StoreError
from .tools import SUPPORTED


class Dispatcher:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.runtime = Runtime(store, self.root)
        self.handles: dict[str, WorkerHandle] = {}

    def bind_integration(self, scope, task_id, artifact_ids, review_ids, **kwargs) -> IntegrationBinding:
        return IntegrationBindings(self.store, self.root).bind(scope, task_id, artifact_ids, review_ids, **kwargs)

    def integration_binding(self, scope, task_id) -> IntegrationBinding:
        return IntegrationBindings(self.store, self.root).get(scope, task_id)

    def bind_review(self, scope, task_id, artifact_id, **kwargs) -> ReviewBinding:
        return ReviewBindings(self.store, self.root).bind(scope, task_id, artifact_id, **kwargs)

    def review_binding(self, scope, task_id) -> ReviewBinding:
        return ReviewBindings(self.store, self.root).get(scope, task_id)

    def binding(self, scope, task_id) -> WorkerBinding:
        self.store.task(scope, task_id)
        row = self.store.db.execute('SELECT data FROM worker_bindings WHERE task_id=? AND current=1', (task_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'worker binding')
        return WorkerBinding.model_validate_json(row['data'])

    def bind(self, scope, task_id, source: Path, *, expected_revision, key, expected_binding_id=None,
             brain='functional', seed=0) -> WorkerBinding:
        store = self.store
        store._role(scope, ('owner',))
        task = store.task(scope, task_id)
        if brain not in BRAIN_REGISTRY:
            raise StoreError('brain')
        try:
            source = Path(source).expanduser().resolve()
        except OSError as error:
            raise StoreError('source_unavailable', 'Approved folder is unavailable') from error
        request = {'bind_worker': task_id, 'source': str(source), 'root': str(self.root), 'revision': expected_revision,
            'previous': expected_binding_id, 'brain': brain, 'seed': seed}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return WorkerBinding.model_validate(saved)
            self._can_bind(scope, task_id, expected_revision, expected_binding_id)
        if not source.is_dir():
            raise StoreError('source_unavailable', 'Approved folder is unavailable')
        if self.root.is_relative_to(source):
            raise StoreError('workspace_location', 'Runtime storage must be outside the approved source folder')
        try:
            snapshot = capture(source)
        except (OSError, ValueError) as error:
            raise StoreError('source_unavailable', 'Approved folder could not be captured') from error
        with store.transaction():
            found, saved, digest = store._replay(scope, key, request)
            if found:
                return WorkerBinding.model_validate(saved)
            task, previous = self._can_bind(scope, task_id, expected_revision, expected_binding_id)
            workflow = store.workflow(scope, task.workflow_id)
            if task.base_snapshot_hash is not None and task.base_snapshot_hash != snapshot.subject_hash:
                raise StoreError('snapshot_changed')
            binding = WorkerBinding(id=uuid4().hex, task_id=task.id, workflow_id=workflow.id,
                project_id=task.project_id, owner_id=scope.actor_id, revision=previous.revision + 1 if previous else 1,
                previous_id=previous.id if previous else None, semantic_revision=task.semantic_revision,
                workflow_semantic_revision=workflow.semantic_revision, contract_hash=task.contract_hash,
                source_root=str(source), runtime_root=str(self.root), source_hash=snapshot.subject_hash,
                brain=brain, seed=seed, created_at=store.clock())
            store.db.execute('UPDATE worker_bindings SET current=0 WHERE task_id=? AND current=1', (task.id,))
            store.db.execute('INSERT INTO worker_bindings VALUES (?,?,?,?)', (binding.id, task.id, 1, binding.model_dump_json()))
            store._event(scope, workflow, 'worker_binding_created', task=task, binding=binding.model_dump(mode='json'))
            store._remember(scope, key, task.project_id, digest, binding)
            return binding

    def _can_bind(self, scope, task_id, expected_revision, expected_binding_id):
        task = self.store.task(scope, task_id)
        if task.revision != expected_revision:
            raise StoreError('revision', 'Stale task', task)
        if task.current_attempt_id is not None or task.state not in {'planned', 'ready', 'retry_wait', 'blocked'}:
            raise StoreError('state', 'An executing or terminal task cannot change its source binding')
        if (task.kind == 'review' or task.permissions is None or not task.permissions.approved
                or task.permissions.read_scopes != ('.',) or set(task.permissions.tools) - (SUPPORTED | {'stop'})):
            raise StoreError('unsupported_profile')
        row = self.store.db.execute('SELECT data FROM worker_bindings WHERE task_id=? AND current=1', (task.id,)).fetchone()
        previous = WorkerBinding.model_validate_json(row['data']) if row else None
        if (previous.id if previous else None) != expected_binding_id:
            raise StoreError('binding_revision', 'Expected the current source approval ID')
        return task, previous

    def tick(self, scope, *, key) -> ClaimResult:
        store = self.store
        store._role(scope, ('runtime',))
        with store.transaction():
            found, saved, _ = store._replay(scope, key, {'dispatch_bound': str(self.root)})
        if found:
            result = ClaimResult.model_validate(saved)
        else:
            approved, reasons, pipeline_blocked = [], [], set()
            from .pipeline import Pipelines
            for workflow in store.workflows(scope):
                blocked = Pipelines(store, self.root).advance(scope, workflow.id)
                reasons.extend(blocked)
                if blocked:
                    pipeline_blocked.add(workflow.id)
            hashes: dict[str, str | None] = {}
            binding: WorkerBinding | ReviewBinding | IntegrationBinding
            for row in store.db.execute('SELECT binding.data FROM worker_bindings binding JOIN tasks ON tasks.id=binding.task_id '
                    'JOIN workflows ON workflows.id=tasks.workflow_id WHERE binding.current=1 AND workflows.slot=1').fetchall():
                binding = WorkerBinding.model_validate_json(row['data'])
                if (binding.project_id not in scope.project_ids or binding.runtime_root != str(self.root)
                        or binding.workflow_id in pipeline_blocked):
                    continue
                task, workflow = store.task(scope, binding.task_id), store.workflow(scope, binding.workflow_id)
                if not binding.matches(task, workflow):
                    reasons.append('execution_binding_stale')
                    continue
                if binding.source_root not in hashes:
                    try:
                        hashes[binding.source_root] = capture(Path(binding.source_root)).subject_hash
                    except (OSError, ValueError):
                        hashes[binding.source_root] = None
                if hashes[binding.source_root] is None:
                    reasons.append('source_unavailable')
                elif hashes[binding.source_root] != binding.source_hash:
                    reasons.append('snapshot_changed')
                else:
                    approved.append(binding.id)
            for row in store.db.execute('SELECT binding.data FROM review_bindings binding JOIN tasks ON tasks.id=binding.task_id '
                    'JOIN workflows ON workflows.id=tasks.workflow_id WHERE binding.current=1 AND workflows.slot=1').fetchall():
                binding = stage_binding(row['data'])
                if (binding.project_id not in scope.project_ids or binding.runtime_root != str(self.root)
                        or binding.workflow_id in pipeline_blocked):
                    continue
                try:
                    service = IntegrationBindings if isinstance(binding, IntegrationBinding) else ReviewBindings
                    service(store, self.root).verify(scope, binding)
                    approved.append(binding.id)
                except (OSError, ValueError) as error:
                    reasons.append(getattr(error, 'code', 'review_binding_unavailable'))
            result = store.dispatch_bound(scope, runtime_root=str(self.root), binding_ids=tuple(approved),
                blocked_reasons=tuple(reasons), key=key)
        if result.attempt is not None:
            self._launch(scope, result.attempt.id)
        return result

    def _launch(self, scope, attempt_id):
        store = self.store
        attempt = store._attempt(attempt_id)
        store.task(scope, attempt.task_id)
        if (attempt.state != 'assigned' or store.db.execute('SELECT 1 FROM execution_lanes WHERE attempt_id=?',
                (attempt_id,)).fetchone()):
            return
        review_mode = store.task(scope, attempt.task_id).kind == 'review'
        if review_mode:
            row = store.db.execute('SELECT data FROM review_bindings WHERE id=?', (attempt.execution_binding_id,)).fetchone()
            binding = stage_binding(row['data'])
        else:
            binding = store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
        try:
            if isinstance(binding, IntegrationBinding):
                IntegrationBindings(store, self.root).verify(scope, binding)
                store.confirm_started(scope, attempt_id, attempt.fence)
                Integration(store, self.root).run(scope, attempt_id, attempt.fence, tuple(binding.artifact_hashes), binding.review_ids,
                    approval_hashes=binding.approval_hashes, reruns=binding.reruns, previous_result_id=binding.previous_result_id)
                return
            if review_mode:
                ReviewBindings(store, self.root).verify(scope, binding)
                store.confirm_started(scope, attempt_id, attempt.fence)
                Reviews(store, self.root).run(scope, attempt_id, attempt.fence, binding.artifact_id,
                    approval_hash=binding.approval_hash, reruns=binding.reruns)
                return
            self.handles[attempt_id] = self.runtime.launch(scope, attempt_id, attempt.fence, Path(binding.source_root),
                brain=binding.brain, seed=binding.seed, expected_source_hash=binding.source_hash)
        except (OSError, ValueError, SubprocessError) as error:
            with store.transaction():
                task = store.task(scope, attempt.task_id)
                workflow = store.workflow(scope, attempt.workflow_id)
                store._event(scope, workflow, 'worker_dispatch_launch_blocked', task=task, attempt=attempt,
                    reason=getattr(error, 'code', type(error).__name__))
                has_effect = store.db.execute('SELECT 1 FROM execution_lanes WHERE attempt_id=?', (attempt_id,)).fetchone()
                worker = store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone()
                current = store._attempt(attempt_id)
                no_review_calls = (review_mode and not store.db.execute('SELECT 1 FROM tool_calls WHERE attempt_id=?', (attempt_id,)).fetchone()
                    and not store.db.execute('SELECT 1 FROM model_calls WHERE attempt_id=?', (attempt_id,)).fetchone())
                if (not has_effect and not worker and (current.started_at is None or no_review_calls)
                        and current.state in {'assigned', 'running', 'stopping', 'reconciling'}):
                    outcome = 'retry_wait' if task.paused or workflow.state == 'pausing' else 'failed'
                    store._confirm_stopped(scope, attempt_id, attempt.fence, outcome=outcome, expenditure_confirmed=True)
            if review_mode and store.db.execute('SELECT 1 FROM review_executions WHERE id=?', (attempt_id,)).fetchone():
                try:
                    Reviews(store, self.root).reconcile(scope, attempt_id)
                except (OSError, ValueError) as recovery_error:
                    with store.transaction():
                        store._event(scope, store.workflow(scope, attempt.workflow_id), 'review_dispatch_recovery_blocked',
                            task=store.task(scope, attempt.task_id), attempt=store._attempt(attempt_id),
                            reason=getattr(recovery_error, 'code', type(recovery_error).__name__))
            if isinstance(binding, IntegrationBinding):
                row = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (attempt_id,)).fetchone()
                if row is not None:
                    try:
                        Integration(store, self.root).reconcile(scope, row['id'])
                    except (OSError, ValueError) as recovery_error:
                        with store.transaction():
                            store._event(scope, store.workflow(scope, attempt.workflow_id), 'integration_dispatch_recovery_blocked',
                                task=store.task(scope, attempt.task_id), attempt=store._attempt(attempt_id),
                                reason=getattr(recovery_error, 'code', type(recovery_error).__name__))
