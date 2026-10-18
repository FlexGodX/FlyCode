"""Exact owner authority for dispatching registered independent artifact reviews."""
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import ReviewBinding, stage_binding
from ..verification.engine import VerificationEngine
from ..verification.plan import contract_hash
from ..verification.snapshot import digest
from .artifacts import Artifacts
from .store import StoreError


class ReviewBindings:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root).resolve()
        self.artifacts = Artifacts(store, self.root)

    @property
    def oracles(self):
        return VerificationEngine(self.root / 'verification').oracles

    def get(self, scope, task_id):
        self.store.task(scope, task_id)
        row = self.store.db.execute('SELECT data FROM review_bindings WHERE task_id=? AND current=1', (task_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'review binding')
        return ReviewBinding.model_validate_json(row['data'])

    def _can_bind(self, scope, task_id, revision, previous_id):
        task = self.store.task(scope, task_id)
        if task.revision != revision:
            raise StoreError('revision', 'Stale task', task)
        if task.current_attempt_id is not None or task.state not in {'planned', 'ready', 'retry_wait', 'blocked'}:
            raise StoreError('state')
        permission = task.permissions
        if (task.kind != 'review' or task.acceptance is None or permission is None or not permission.approved
                or permission.read_scopes != ('.',) or permission.write_scopes or set(permission.tools) != {'run_tests'}):
            raise StoreError('unsupported_profile')
        row = self.store.db.execute('SELECT data FROM review_bindings WHERE task_id=? AND current=1', (task_id,)).fetchone()
        previous = stage_binding(row['data']) if row else None
        if (previous.id if previous else None) != previous_id:
            raise StoreError('binding_revision')
        return task, previous

    def _pins(self, scope, task, artifact):
        author = self.store._attempt(artifact.attempt_id)
        original = self.store.task(scope, artifact.task_id)
        if (artifact.workflow_id != task.workflow_id or task.input_artifacts != {artifact.id: artifact.patch_hash}
                or original.acceptance is None or task.acceptance is None
                or contract_hash(original.acceptance) != contract_hash(task.acceptance)
                or author.task_id != artifact.task_id or author.workflow_id != artifact.workflow_id
                or author.semantic_revision != artifact.semantic_revision or author.contract_hash != artifact.contract_hash
                or author.fence != artifact.fence or not author.stop_confirmed):
            raise StoreError('review_scope')
        return author

    def bind(self, scope, task_id, artifact_id, *, approval_hash, expected_revision, key, expected_binding_id=None, reruns=0):
        store = self.store
        store._role(scope, ('owner',))
        store.task(scope, task_id)
        request = {'bind_review': task_id, 'artifact': artifact_id, 'approval': approval_hash, 'revision': expected_revision,
            'previous': expected_binding_id, 'reruns': reruns, 'root': str(self.root)}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return ReviewBinding.model_validate(saved)
            task, _ = self._can_bind(scope, task_id, expected_revision, expected_binding_id)
        artifact = self.artifacts.get(scope, artifact_id)
        self._pins(scope, task, artifact)
        try:
            self.oracles.resolve(approval_hash, task.acceptance)
        except ValueError as error:
            raise StoreError('oracle_changed') from error
        with store.transaction():
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return ReviewBinding.model_validate(saved)
            task, previous = self._can_bind(scope, task_id, expected_revision, expected_binding_id)
            current = self.artifacts._record(scope, artifact_id)
            if digest(current.model_dump(mode='json', exclude_unset=True)) != digest(artifact.model_dump(mode='json', exclude_unset=True)):
                raise StoreError('artifact_integrity')
            author = self._pins(scope, task, current)
            workflow = store.workflow(scope, task.workflow_id)
            binding = ReviewBinding(id=uuid4().hex, task_id=task.id, workflow_id=workflow.id, project_id=task.project_id,
                owner_id=scope.actor_id, revision=previous.revision + 1 if previous else 1, previous_id=previous.id if previous else None,
                semantic_revision=task.semantic_revision, workflow_semantic_revision=workflow.semantic_revision,
                contract_hash=task.contract_hash, runtime_root=str(self.root), artifact_id=artifact.id, patch_hash=artifact.patch_hash,
                artifact_hash=digest(artifact.model_dump(mode='json', exclude_unset=True)), author_attempt_id=author.id, author_fence=author.fence,
                author_instance_id=author.instance_id,
                author_semantic_revision=store.task(scope, artifact.task_id).semantic_revision,
                author_contract_hash=store.task(scope, artifact.task_id).contract_hash, approval_hash=approval_hash, reruns=reruns, created_at=store.clock())
            store.db.execute('UPDATE review_bindings SET current=0 WHERE task_id=? AND current=1', (task.id,))
            store.db.execute('INSERT INTO review_bindings VALUES (?,?,?,?)', (binding.id, task.id, 1, binding.model_dump_json()))
            store._event(scope, workflow, 'review_binding_created', task=task, binding=binding.model_dump(mode='json'))
            store._remember(scope, key, task.project_id, request_hash, binding)
            return binding

    def verify_metadata(self, scope, binding):
        task = self.store.task(scope, binding.task_id)
        workflow = self.store.workflow(scope, binding.workflow_id)
        if not binding.matches(task, workflow):
            raise StoreError('execution_binding_stale')
        artifact = self.artifacts._record(scope, binding.artifact_id)
        author = self._pins(scope, task, artifact)
        hashes = {digest(artifact.model_dump(mode='json', exclude_unset=True))}
        if 'continuation' not in artifact.model_fields_set:
            # Retained bindings used the full pre-continuation model, including default prerequisites.
            hashes.add(digest(artifact.model_dump(mode='json', exclude={'continuation'})))
        if (binding.artifact_hash not in hashes or author.id != binding.author_attempt_id
                or author.instance_id != binding.author_instance_id or author.fence != binding.author_fence
                or self.store.task(scope, artifact.task_id).semantic_revision != binding.author_semantic_revision
                or self.store.task(scope, artifact.task_id).contract_hash != binding.author_contract_hash):
            raise StoreError('artifact_integrity')
        return binding

    def verify(self, scope, binding):
        self.verify_metadata(scope, binding)
        self.artifacts.get(scope, binding.artifact_id)
        task = self.store.task(scope, binding.task_id)
        try:
            self.oracles.resolve(binding.approval_hash, task.acceptance)
        except ValueError as error:
            raise StoreError('oracle_changed') from error
        return binding
