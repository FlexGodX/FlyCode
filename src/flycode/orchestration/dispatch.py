"""Host-approved source bindings and baseline dispatch through the canonical store."""

from pathlib import Path
from subprocess import SubprocessError
from uuid import uuid4

from ..brain import BRAIN_REGISTRY
from ..domain.orchestration import ClaimResult, WorkerBinding
from ..verification.snapshot import capture
from .runtime import Runtime, WorkerHandle
from .store import Store, StoreError
from .tools import SUPPORTED


class Dispatcher:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.runtime = Runtime(store, self.root)
        self.handles: dict[str, WorkerHandle] = {}

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
            approved, reasons = [], []
            hashes: dict[str, str | None] = {}
            for row in store.db.execute('SELECT binding.data FROM worker_bindings binding JOIN tasks ON tasks.id=binding.task_id '
                    'JOIN workflows ON workflows.id=tasks.workflow_id WHERE binding.current=1 AND workflows.slot=1').fetchall():
                binding = WorkerBinding.model_validate_json(row['data'])
                if binding.project_id not in scope.project_ids or binding.runtime_root != str(self.root):
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
        binding = store._load('worker_bindings', attempt.execution_binding_id, WorkerBinding)
        try:
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
                if (not has_effect and not worker and current.started_at is None
                        and current.state in {'assigned', 'stopping', 'reconciling'}):
                    outcome = 'retry_wait' if task.paused or workflow.state == 'pausing' else 'failed'
                    store._confirm_stopped(scope, attempt_id, attempt.fence, outcome=outcome, expenditure_confirmed=True)
