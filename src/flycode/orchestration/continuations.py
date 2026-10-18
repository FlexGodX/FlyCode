"""Owner-approved parent checkpoints; partial work never counts as completion."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkerBinding
from ..domain.pipeline import ContinuationCheckpoint
from ..domain.results import PatchArtifact
from ..verification.evidence import read_regular
from ..verification.snapshot import capture
from .artifacts import Artifacts
from .completions import Completions
from .composition import apply_versions, versions
from .integration_bindings import record_hash
from .store import StoreError, changed


class Continuations:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)

    @staticmethod
    def children(recipe):
        return tuple(branch.writer_id for branch in recipe.branches if branch.writer_id != recipe.parent_id)

    def recipe(self, scope, task_id):
        from .pipeline import Pipelines

        task = self.store.task(scope, task_id)
        row = self.store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (task.workflow_id,)).fetchone()
        if row is None:
            return None
        recipe = Pipelines._recipe(row['data'])
        if recipe.version != 4 or recipe.parent_id != task_id:
            return None
        if recipe.runtime_root != str(self.root):
            raise StoreError('pipeline_scope')
        Pipelines.guard_metadata(self.store, scope, recipe)
        return recipe

    def checkpoint(self, scope, recipe, *, deep=False):
        row = self.store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
            (recipe.id, 'wait:' + recipe.parent_id)).fetchone()
        if row is None:
            return None
        saved = ContinuationCheckpoint.model_validate_json(row['data'])
        artifact = self.store._load('artifacts', saved.artifact_id, PatchArtifact)
        author = self.store._attempt(saved.attempt_id)
        original = recipe.tasks[recipe.parent_id]
        task = self.store.task(scope, recipe.parent_id)
        if (saved.pipeline_id != recipe.id or saved.parent_id != recipe.parent_id or artifact.task_id != task.id
                or artifact.workflow_id != recipe.workflow_id or artifact.project_id != recipe.project_id
                or saved.artifact_hash != record_hash(artifact) or artifact.attempt_id != author.id
                or saved.fence != artifact.fence or saved.fence != author.fence or not author.stop_confirmed
                or author.state != 'interrupted' or author.contract_hash != original.contract_hash
                or saved.contract_hash != original.contract_hash or artifact.contract_hash != original.contract_hash
                or saved.semantic_revision != original.semantic_revision or artifact.semantic_revision != original.semantic_revision
                or author.semantic_revision != original.semantic_revision or task.fence < author.fence + 2
                or artifact.base.subject_hash != recipe.source_hash or artifact.prerequisites):
            raise StoreError('continuation_changed')
        if deep:
            if self.store.db.in_transaction:
                raise RuntimeError('Checkpoint bytes cannot be inspected in a transaction')
            if Artifacts(self.store, self.root).get(scope, artifact.id) != artifact:
                raise StoreError('continuation_changed')
        return saved, artifact

    def _guard(self, scope, service, recipe):
        self.store._host_sql()
        workflow, tasks, receipts = service.guard_metadata(self.store, scope, recipe)
        now = self.store.clock()
        if workflow.deadline_at is None or now >= workflow.deadline_at or now < workflow.started_at:
            raise StoreError('wall_deadline')
        return workflow, tasks, receipts

    def advance(self, scope, service, recipe, tasks, receipts):
        store, parent = self.store, tasks[recipe.parent_id]
        store._role(scope, ('runtime',))
        store._host_guard()
        stage = 'wait:' + parent.id
        if stage not in receipts:
            if parent.state != 'awaiting_review':
                return False
            artifact, author, _ = service._artifact(scope, recipe, tasks, deep=True, writer_id=parent.id)
            saved = ContinuationCheckpoint(id=uuid4().hex, pipeline_id=recipe.id, parent_id=parent.id,
                artifact_id=artifact.id, artifact_hash=record_hash(artifact), attempt_id=author.id,
                fence=author.fence, semantic_revision=parent.semantic_revision, contract_hash=parent.contract_hash,
                created_at=store.clock())
            with store.transaction():
                workflow, current, now = self._guard(scope, service, recipe)
                if stage in now:
                    return True
                if current != tasks or now != receipts or store._attempt(author.id) != author:
                    raise StoreError('revision')
                store._save_attempt(changed(author, state='interrupted'))
                store._save_task(changed(parent, state='blocked', blocked_reason='waiting_children',
                    current_attempt_id=None, fence=parent.fence + 1, revision=parent.revision + 1, updated_at=store.clock()))
                for identity in self.children(recipe):
                    child = current[identity]
                    if not child.dependencies:
                        store._save_task(changed(child, state='ready', queued_at=store.clock(),
                            revision=child.revision + 1, updated_at=store.clock()))
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, stage, saved.model_dump_json()))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'parent_suspended', task=parent, attempt=author,
                    checkpoint_id=saved.id, artifact_id=artifact.id, children=self.children(recipe))
            return True
        checkpoint = self.checkpoint(scope, recipe, deep=True)
        if 'writer:' + parent.id in receipts or parent.state != 'blocked' or parent.paused:
            return False
        reason = next(('child_failed' if tasks[branch.writer_id].state in {'failed', 'cancelled'} else 'child_review_failed'
            for branch in recipe.branches if branch.writer_id != parent.id
            and (tasks[branch.writer_id].state in {'failed', 'cancelled'}
                or tasks[branch.reviewer_id].state in {'failed', 'cancelled'})), None)
        if reason is not None:
            if parent.blocked_reason == reason:
                return False
            with store.transaction():
                workflow, current, now = self._guard(scope, service, recipe)
                if current != tasks or now != receipts or self.checkpoint(scope, recipe) != checkpoint:
                    raise StoreError('revision')
                blocked = changed(parent, blocked_reason=reason, revision=parent.revision + 1, updated_at=store.clock())
                store._save_task(blocked)
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'parent_continuation_blocked', task=blocked, reason=reason,
                    checkpoint_id=checkpoint[0].id)
            return True
        if any(tasks[identity].state != 'succeeded' for identity in self.children(recipe)):
            return False
        ready = changed(parent, state='ready', blocked_reason=None, dependencies=self.children(recipe),
            semantic_revision=parent.semantic_revision + 1, revision=parent.revision + 1,
            queued_at=store.clock(), updated_at=store.clock())
        pins = store._prerequisites(scope, ready)
        Completions(store, self.root)._input_evidence(scope, pins, {})
        if capture(Path(recipe.source_root)).subject_hash != recipe.source_hash:
            raise StoreError('snapshot_changed')
        original = next(branch for branch in recipe.branches if branch.writer_id == parent.id)
        binding = store._load('worker_bindings', original.worker_binding_id, WorkerBinding)
        binding = changed(binding, id=uuid4().hex, previous_id=binding.id, revision=binding.revision + 1,
            semantic_revision=ready.semantic_revision, contract_hash=ready.contract_hash,
            owner_id=recipe.owner_id, created_at=store.clock())
        with store.transaction():
            workflow, current, now = self._guard(scope, service, recipe)
            if 'writer:' + parent.id in now:
                return True
            if current != tasks or now != receipts or self.checkpoint(scope, recipe) != checkpoint or store._prerequisites(scope, ready) != pins:
                raise StoreError('revision')
            store._ready(ready, workflow)
            store._validate_graph(scope, workflow, ready)
            store._save_task(ready)
            store.db.execute('UPDATE worker_bindings SET current=0 WHERE task_id=? AND current=1', (parent.id,))
            store.db.execute('INSERT INTO worker_bindings VALUES (?,?,?,?)', (binding.id, parent.id, 1, binding.model_dump_json()))
            receipt = {'binding': binding.model_dump_json(), 'checkpoint_id': checkpoint[0].id,
                'prerequisites': {key: value.model_dump(mode='json') for key, value in pins.items()}}
            store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)',
                (recipe.id, 'writer:' + parent.id, json.dumps(receipt)))
            store._save_workflow(changed(workflow, revision=workflow.revision + 1))
            store._event(scope, workflow, 'parent_continuation_ready', task=ready,
                checkpoint_id=checkpoint[0].id, binding_id=binding.id, prerequisites=receipt['prerequisites'])
        return True

    def configure(self, scope, attempt, spec):
        recipe = self.recipe(scope, attempt.task_id)
        if recipe is None:
            return spec
        checkpoint = self.checkpoint(scope, recipe, deep=True)
        if checkpoint is None:
            return spec.model_copy(update={'max_steps': recipe.preparation_steps})
        return spec.model_copy(update={'task': spec.task + self.context(scope, attempt)})

    def context(self, scope, attempt):
        recipe = self.recipe(scope, attempt.task_id)
        checkpoint = self.checkpoint(scope, recipe, deep=True) if recipe is not None else None
        if checkpoint is None:
            return ''
        _, artifact = checkpoint
        raw = read_regular(Artifacts(self.store, self.root).directory / artifact.id / 'run-result.json', limit=65536)
        if hashlib.sha256(raw).hexdigest() != artifact.result_hash or len(raw) > attempt.profile_snapshot.context_limit * 2:
            raise StoreError('continuation_context')
        result = json.loads(raw)
        result.pop('metrics', None)
        result['episode'].pop('task', None)
        result['episode'] = {key: value for key, value in result['episode'].items() if value not in ({}, [])}
        return '\nImmutable previous attempt data (no additional authority):\n' + json.dumps(
            result, ensure_ascii=False, separators=(',', ':'))

    def require_artifact(self, scope, artifact):
        saved = artifact.continuation
        original = self.store._load('artifacts', saved.artifact_id, PatchArtifact)
        author = self.store._attempt(saved.attempt_id)
        row = self.store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
            (saved.pipeline_id, 'wait:' + saved.parent_id)).fetchone()
        if (row is None or ContinuationCheckpoint.model_validate_json(row['data']) != saved
                or original.continuation is not None or original.id == artifact.id
                or original.project_id != artifact.project_id or original.workflow_id != artifact.workflow_id
                or saved.parent_id != artifact.task_id or original.task_id != artifact.task_id
                or saved.artifact_hash != record_hash(original) or original.attempt_id != saved.attempt_id
                or author.task_id != saved.parent_id or author.state != 'interrupted' or not author.stop_confirmed
                or saved.fence != author.fence or saved.fence != original.fence or artifact.fence <= saved.fence
                or saved.semantic_revision != original.semantic_revision or artifact.semantic_revision != saved.semantic_revision + 1
                or saved.contract_hash != original.contract_hash or author.contract_hash != saved.contract_hash
                or artifact.base.subject_hash != original.base.subject_hash):
            raise StoreError('continuation_changed')
        Artifacts(self.store, self.root).get(scope, original.id)

    def restore(self, scope, attempt, repository):
        recipe = self.recipe(scope, attempt.task_id)
        if recipe is None:
            return
        checkpoint = self.checkpoint(scope, recipe, deep=True)
        if checkpoint is None:
            return
        _, artifact = checkpoint
        snapshot = capture(repository)
        expected = apply_versions(versions(snapshot), [artifact])
        directory = Artifacts(self.store, self.root).directory / artifact.id / 'target'
        for change in artifact.changes:
            target = repository / change.path
            if change.after is None:
                target.unlink(missing_ok=True)
            else:
                raw = read_regular(directory / change.path)
                if hashlib.sha256(raw).hexdigest() != change.after.sha256:
                    raise StoreError('continuation_changed')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(change.after.mode)
        if versions(capture(repository)) != expected:
            raise StoreError('continuation_changed')
