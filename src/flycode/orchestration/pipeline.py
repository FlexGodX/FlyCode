"""Bounded runtime materialization delegated by exact, durable owner authority."""
import json
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import (
    IntegrationBinding,
    ReviewBinding,
    WorkerBinding,
    stage_binding,
)
from ..domain.pipeline import PipelineBranch, PipelineRecipe, TeamPipelineRecipe
from ..verification.plan import contract_hash
from ..verification.snapshot import capture
from .inputs import Inputs
from .integration import Integration
from .integration_bindings import IntegrationBindings, record_hash
from .review_bindings import ReviewBindings
from .store import StoreError, changed, required_task


class Pipelines:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root).resolve()
        self.reviews = ReviewBindings(store, self.root)
        self.integration_bindings = IntegrationBindings(store, self.root)

    def get(self, scope, identity):
        row = self.store.db.execute('SELECT data FROM pipelines WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'pipeline')
        model = TeamPipelineRecipe if json.loads(row['data']).get('version') == 2 else PipelineRecipe
        recipe = model.model_validate_json(row['data'])
        self.store.workflow(scope, recipe.workflow_id)
        if recipe.runtime_root != str(self.root):
            raise StoreError('pipeline_scope')
        return recipe

    @staticmethod
    def _branches(recipe):
        if isinstance(recipe, TeamPipelineRecipe):
            return recipe.branches
        return (PipelineBranch(writer_id=recipe.writer_id, reviewer_id=recipe.reviewer_id,
            worker_binding_id=recipe.worker_binding_id, worker_binding_hash=recipe.worker_binding_hash),)

    @staticmethod
    def _review_stage(recipe, branch):
        return 'review' if recipe.version == 1 else 'review:' + branch.reviewer_id

    def approve_team(self, scope, workflow_id, pairs, integrator_id, *, source,
                     expected_revisions, approval_hashes, key, reruns=0):
        store = self.store
        store._role(scope, ('owner',))
        store.workflow(scope, workflow_id)
        request = {'team_pipeline': workflow_id, 'pairs': pairs, 'integrator': integrator_id,
            'revisions': expected_revisions, 'approvals': approval_hashes, 'source': str(source),
            'root': str(self.root), 'reruns': reruns}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return TeamPipelineRecipe.model_validate(saved)
            workflow, tasks, profiles, bindings = self._initial_many(scope, workflow_id, pairs,
                integrator_id, expected_revisions)
        source = Path(source).resolve(strict=True)
        first = next(iter(bindings.values()))
        if str(source) != first.source_root or capture(source).subject_hash != first.source_hash:
            raise StoreError('snapshot_changed')
        if set(approval_hashes) != set(tasks):
            raise StoreError('approval_scope')
        for identity, approval in approval_hashes.items():
            self.reviews.oracles.resolve(approval, tasks[identity].acceptance)
        with store.transaction():
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return TeamPipelineRecipe.model_validate(saved)
            if self._initial_many(scope, workflow_id, pairs, integrator_id, expected_revisions) != (workflow, tasks, profiles, bindings):
                raise StoreError('revision')
            recipe = TeamPipelineRecipe(id=uuid4().hex, owner_id=scope.actor_id, project_id=workflow.project_id,
                workflow_id=workflow.id, workflow_semantic_revision=workflow.semantic_revision, workflow_budget=workflow.budget,
                branches=tuple(PipelineBranch(writer_id=writer, reviewer_id=pairs[writer], worker_binding_id=bindings[writer].id,
                    worker_binding_hash=record_hash(bindings[writer])) for writer in sorted(pairs)),
                integrator_id=integrator_id, tasks=tasks, profiles=profiles, source_root=str(source),
                runtime_root=str(self.root), source_hash=first.source_hash, approval_hashes=approval_hashes,
                reruns=reruns, approval_sequence=0, created_at=store.clock())
            store._event(scope, workflow, 'pipeline_approved', recipe_id=recipe.id)
            recipe = changed(recipe, approval_sequence=store.db.execute('SELECT MAX(sequence) FROM events').fetchone()[0])
            store.db.execute('INSERT INTO pipelines VALUES (?,?,?)', (recipe.id, workflow.id, recipe.model_dump_json()))
            for identity in (workflow.root_task_id, *sorted(pairs)):
                task = tasks[identity]
                store._save_task(changed(task, state='ready', queued_at=store.clock(), revision=task.revision + 1,
                    updated_at=store.clock()))
                store._event(scope, workflow, 'task_enqueue', task=task, recipe_id=recipe.id)
            store._save_workflow(changed(workflow, revision=workflow.revision + 1))
            store._remember(scope, key, workflow.project_id, request_hash, recipe)
            return recipe

    def approve(self, scope, workflow_id, writer_id, reviewer_id, integrator_id, *, source,
                expected_revisions, approval_hashes, key, reruns=0):
        store = self.store
        store._role(scope, ('owner',))
        workflow = store.workflow(scope, workflow_id)
        request = {'pipeline': workflow_id, 'writer': writer_id, 'reviewer': reviewer_id, 'integrator': integrator_id,
            'revisions': expected_revisions, 'approvals': approval_hashes, 'source': str(source),
            'root': str(self.root), 'reruns': reruns}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return PipelineRecipe.model_validate(saved)
            workflow, tasks, profiles, binding = self._initial(scope, workflow_id, writer_id, reviewer_id,
                integrator_id, expected_revisions)
        source = Path(source).resolve(strict=True)
        if str(source) != binding.source_root or capture(source).subject_hash != binding.source_hash:
            raise StoreError('snapshot_changed')
        if set(approval_hashes) != set(tasks):
            raise StoreError('approval_scope')
        oracles = self.reviews.oracles
        for identity, approval in approval_hashes.items():
            oracles.resolve(approval, tasks[identity].acceptance)
        with store.transaction():
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return PipelineRecipe.model_validate(saved)
            current, now, current_profiles, current_binding = self._initial(scope, workflow_id, writer_id,
                reviewer_id, integrator_id, expected_revisions)
            if (current != workflow or now != tasks or current_profiles != profiles or current_binding != binding):
                raise StoreError('revision')
            recipe = PipelineRecipe(id=uuid4().hex, owner_id=scope.actor_id, project_id=workflow.project_id,
                workflow_id=workflow.id, workflow_semantic_revision=workflow.semantic_revision, workflow_budget=workflow.budget,
                writer_id=writer_id, reviewer_id=reviewer_id, integrator_id=integrator_id, tasks=tasks, profiles=profiles,
                worker_binding_id=binding.id, worker_binding_hash=record_hash(binding), source_root=str(source),
                runtime_root=str(self.root), source_hash=binding.source_hash, approval_hashes=approval_hashes,
                reruns=reruns, approval_sequence=0, created_at=store.clock())
            store._event(scope, workflow, 'pipeline_approved', recipe_id=recipe.id)
            recipe = changed(recipe, approval_sequence=store.db.execute('SELECT MAX(sequence) FROM events').fetchone()[0])
            store.db.execute('INSERT INTO pipelines VALUES (?,?,?)', (recipe.id, workflow.id, recipe.model_dump_json()))
            for identity in (workflow.root_task_id, writer_id):
                task = tasks[identity]
                store._save_task(changed(task, state='ready', queued_at=store.clock(), revision=task.revision + 1,
                    updated_at=store.clock()))
                store._event(scope, workflow, 'task_enqueue', task=task, recipe_id=recipe.id)
            store._save_workflow(changed(workflow, revision=workflow.revision + 1))
            store._remember(scope, key, workflow.project_id, request_hash, recipe)
            return recipe

    def _initial(self, scope, workflow_id, writer_id, reviewer_id, integrator_id, revisions):
        workflow, tasks, profiles, bindings = self._initial_many(scope, workflow_id, {writer_id: reviewer_id},
            integrator_id, revisions)
        return workflow, tasks, profiles, bindings[writer_id]

    def _initial_many(self, scope, workflow_id, pairs, integrator_id, revisions):
        store = self.store
        workflow = store.workflow(scope, workflow_id)
        tasks = {item.id: item for item in store.tasks(scope, workflow_id)}
        ids = {workflow.root_task_id, integrator_id, *pairs, *pairs.values()}
        if (not 1 <= len(pairs) <= 3 or len(ids) != 2 * len(pairs) + 2
                or set(tasks) != ids or set(revisions) != ids or workflow.state != 'draft'):
            raise StoreError('pipeline_scope')
        if store.db.execute('SELECT 1 FROM pipelines WHERE workflow_id=?', (workflow_id,)).fetchone():
            raise StoreError('pipeline_exists')
        profiles = {}
        for identity, task in tasks.items():
            if task.revision != revisions[identity]:
                raise StoreError('revision')
            if (task.state != 'planned' or task.current_attempt_id is not None or task.paused or task.archived
                    or not required_task(task) or task.input_artifacts):
                raise StoreError('pipeline_state')
            store._ready(task, workflow)
            if identity == workflow.root_task_id:
                continue
            if task.parent_id != workflow.root_task_id or not task.allowed_profile_ids:
                raise StoreError('pipeline_scope')
            role = 'developer' if identity in pairs else 'reviewer'
            if task.kind != ('implementation' if identity in pairs else 'review'):
                raise StoreError('pipeline_scope')
            for profile_id in task.allowed_profile_ids:
                profile = store._profile(profile_id)
                if (profile.role != role or task.project_id not in profile.project_ids or not store._binding_current(profile)
                        or not profile.permissions.approved):
                    raise StoreError('profile_scope')
                profiles[profile.id] = profile
            if role == 'reviewer':
                self.reviews._can_bind(scope, identity, task.revision, None)
        if set(tasks[integrator_id].dependencies) != set(pairs.values()):
            raise StoreError('pipeline_scope')
        bindings = {}
        for writer_id, reviewer_id in pairs.items():
            if (tasks[writer_id].dependencies or tasks[reviewer_id].dependencies
                    or contract_hash(tasks[writer_id].acceptance) != contract_hash(tasks[reviewer_id].acceptance)):
                raise StoreError('pipeline_scope')
            row = store.db.execute('SELECT id FROM worker_bindings WHERE task_id=? AND current=1', (writer_id,)).fetchone()
            if row is None:
                raise StoreError('binding_required')
            binding = store._load('worker_bindings', row['id'], WorkerBinding)
            if not binding.matches(tasks[writer_id], workflow) or binding.runtime_root != str(self.root):
                raise StoreError('execution_binding_stale')
            bindings[writer_id] = binding
        if len({(binding.source_root, binding.source_hash) for binding in bindings.values()}) != 1:
            raise StoreError('pipeline_scope')
        return workflow, tasks, profiles, bindings

    def _receipts(self, recipe):
        return {row['stage']: json.loads(row['data']) for row in self.store.db.execute(
            'SELECT stage,data FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,))}

    def _guard(self, scope, recipe):
        store = self.store
        workflow = store.workflow(scope, recipe.workflow_id)
        if (workflow.semantic_revision != recipe.workflow_semantic_revision
                or workflow.budget != recipe.workflow_budget):
            raise StoreError('pipeline_generation')
        if workflow.state not in {'draft', 'running'} or store._counter('queue_paused'):
            raise StoreError('paused')
        tasks = {item.id: item for item in store.tasks(scope, workflow.id)}
        if set(tasks) != set(recipe.tasks):
            raise StoreError('pipeline_scope')
        receipts = self._receipts(recipe)
        by_id = {stage_binding(value['binding']).task_id: stage_binding(value['binding']) for value in receipts.values()}
        for identity, task in tasks.items():
            expected = by_id.get(identity, recipe.tasks[identity])
            if (task.semantic_revision != expected.semantic_revision or task.contract_hash != expected.contract_hash
                    or task.paused or task.archived or not required_task(task)
                    or task.permissions is None or not task.permissions.approved):
                raise StoreError('pipeline_changed')
            if identity in by_id:
                row = store.db.execute('SELECT id FROM review_bindings WHERE task_id=? AND current=1', (identity,)).fetchone()
                if row is None or row['id'] != by_id[identity].id:
                    raise StoreError('binding_revision')
        for identity, expected in recipe.profiles.items():
            if store._profile(identity) != expected or not store._binding_current(expected):
                raise StoreError('model_unavailable')
        for branch in self._branches(recipe):
            row = store.db.execute('SELECT id,data FROM worker_bindings WHERE task_id=? AND current=1', (branch.writer_id,)).fetchone()
            if row is None or row['id'] != branch.worker_binding_id or record_hash(WorkerBinding.model_validate_json(row['data'])) != branch.worker_binding_hash:
                raise StoreError('binding_revision')
        return workflow, tasks, receipts

    def _artifact(self, scope, recipe, tasks, *, deep, writer_id=None):
        store = self.store
        task = tasks[writer_id or recipe.writer_id]
        if task.state != 'awaiting_review' or task.current_attempt_id is None:
            raise StoreError('pipeline_wait')
        author = store._attempt(task.current_attempt_id)
        if (author.state != 'awaiting_review' or not author.stop_confirmed or task.fence != author.fence + 1
                or author.semantic_revision != task.semantic_revision or author.contract_hash != task.contract_hash):
            raise StoreError('fence')
        row = store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (author.id,)).fetchone()
        if row is None:
            raise StoreError('pipeline_wait')
        service = self.reviews.artifacts
        artifact = service.get(scope, row['id']) if deep else service._record(scope, row['id'])
        event = store.db.execute("SELECT sequence FROM events WHERE workflow_id=? AND json_extract(data,'$.kind')='artifact_published' "
            "AND json_extract(data,'$.attempt_id')=? AND json_extract(data,'$.data.artifact_id')=?", (recipe.workflow_id, author.id, artifact.id)).fetchone()
        if (event is None or event['sequence'] <= recipe.approval_sequence or artifact.fence != author.fence
                or artifact.base.subject_hash != recipe.source_hash or artifact.task_id != task.id
                or artifact.contract_hash != task.contract_hash or artifact.semantic_revision != task.semantic_revision):
            raise StoreError('artifact_integrity')
        return artifact, author, event['sequence']

    def advance(self, scope, workflow_id):
        store = self.store
        store._role(scope, ('runtime',))
        store.workflow(scope, workflow_id)
        rows = store.db.execute('SELECT id FROM pipelines WHERE workflow_id=?', (workflow_id,)).fetchall()
        if not rows or store.workflow(scope, workflow_id).state == 'succeeded':
            return ()
        try:
            recipe = self.get(scope, rows[0]['id'])
            workflow, tasks, receipts = self._guard(scope, recipe)
            service = self.reviews
            for identity, approval in recipe.approval_hashes.items():
                service.oracles.resolve(approval, tasks[identity].acceptance)
            if workflow.state == 'draft':
                if not store.db.execute('SELECT 1 FROM workflows WHERE slot=1').fetchone():
                    store.start_next(scope, key='pipeline-start:' + uuid4().hex)
                return ()
            if 'integration' in receipts:
                return ()
            branches, outputs = self._branches(recipe), {}
            for branch in branches:
                try:
                    outputs[branch.writer_id] = self._artifact(scope, recipe, tasks, deep=True, writer_id=branch.writer_id)
                except StoreError as error:
                    if error.code != 'pipeline_wait':
                        raise
            pending = [branch for branch in branches if self._review_stage(recipe, branch) not in receipts
                and branch.writer_id in outputs]
            if pending:
                selected = pending[0]
                stage, identity = self._review_stage(recipe, selected), selected.reviewer_id
                outputs = {selected.writer_id: outputs[selected.writer_id]}
                review_mode = True
            else:
                if len(outputs) != len(branches) or any(self._review_stage(recipe, branch) not in receipts for branch in branches):
                    return ()
                stage, identity, review_mode = 'integration', recipe.integrator_id, False
            for artifact, _, _ in outputs.values():
                Inputs(store).require_artifact(scope, artifact)
            if capture(Path(recipe.source_root)).subject_hash != recipe.source_hash:
                raise StoreError('snapshot_changed')
            task = tasks[identity]
            if task.state != 'planned' or task.current_attempt_id is not None:
                raise StoreError('pipeline_state')
            task = changed(task, input_artifacts={artifact.id: artifact.patch_hash for artifact, _, _ in outputs.values()},
                semantic_revision=task.semantic_revision + 1, revision=task.revision + 1)
            common = {'id': uuid4().hex, 'task_id': task.id, 'workflow_id': workflow.id, 'project_id': task.project_id,
                'owner_id': recipe.owner_id, 'revision': 1, 'semantic_revision': task.semantic_revision,
                'workflow_semantic_revision': workflow.semantic_revision, 'contract_hash': task.contract_hash,
                'runtime_root': str(self.root), 'reruns': recipe.reruns, 'created_at': store.clock()}
            if review_mode:
                artifact, author, sequence = next(iter(outputs.values()))
                service._pins(scope, task, artifact)
                binding = ReviewBinding(**common, artifact_id=artifact.id, patch_hash=artifact.patch_hash,
                    artifact_hash=record_hash(artifact), author_attempt_id=author.id, author_fence=author.fence,
                    author_instance_id=author.instance_id, author_semantic_revision=tasks[artifact.task_id].semantic_revision,
                    author_contract_hash=tasks[artifact.task_id].contract_hash, approval_hash=recipe.approval_hashes[task.id])
            else:
                integrated = Integration(store, self.root)
                reviews = []
                for branch in branches:
                    reviewer = tasks[branch.reviewer_id]
                    if reviewer.state != 'succeeded' or reviewer.current_attempt_id is not None:
                        raise StoreError('pipeline_wait')
                    row = store.db.execute('SELECT id FROM reviews WHERE attempt_id IN (SELECT id FROM attempts WHERE task_id=?)',
                        (reviewer.id,)).fetchall()
                    if len(row) != 1:
                        raise StoreError('review_changed')
                    reviews.append(integrated.reviews.get(scope, row[0]['id']))
                expected = {**tasks, task.id: task}
                binding = IntegrationBinding(**common, source_root=recipe.source_root, source_hash=recipe.source_hash,
                    artifact_hashes={artifact.id: artifact.patch_hash for artifact, _, _ in outputs.values()},
                    artifact_records={artifact.id: record_hash(artifact) for artifact, _, _ in outputs.values()},
                    review_ids=tuple(review.id for review in reviews), review_records={review.id: record_hash(review) for review in reviews},
                    author_attempts={writer: author.id for writer, (_, author, _) in outputs.items()},
                    contracts={key: item.contract_hash for key, item in expected.items()},
                    semantic_revisions={key: item.semantic_revision for key, item in expected.items()},
                    approval_hashes={key: recipe.approval_hashes[key] for key in (workflow.root_task_id, *outputs, task.id)})
                integrated._reviewed(scope, binding)
            with store.transaction():
                workflow, current, now = self._guard(scope, recipe)
                if stage in now:
                    return ()
                for writer, (artifact, _, sequence) in outputs.items():
                    again, _, current_sequence = self._artifact(scope, recipe, current, deep=False, writer_id=writer)
                    if record_hash(again) != record_hash(artifact) or current_sequence != sequence:
                        raise StoreError('revision')
                if now != receipts:
                    raise StoreError('revision')
                target = current[identity]
                if target.state != 'planned' or target.current_attempt_id is not None:
                    raise StoreError('pipeline_state')
                if current != tasks:
                    raise StoreError('revision')
                task = changed(task, state='ready', queued_at=store.clock(), updated_at=store.clock())
                store._validate_graph(scope, workflow, task)
                store._ready(task, workflow)
                store._save_task(task)
                if review_mode:
                    service.verify_metadata(scope, binding)
                else:
                    self.integration_bindings.verify_metadata(scope, binding)
                store.db.execute('INSERT INTO review_bindings VALUES (?,?,?,?)', (binding.id, task.id, 1, binding.model_dump_json()))
                if recipe.version == 1 or review_mode:
                    artifact, _, sequence = next(iter(outputs.values()))
                    receipt = {'binding': binding.model_dump_json(), 'artifact_sequence': sequence, 'artifact_id': artifact.id}
                    provenance = {'artifact_id': artifact.id, 'producer_sequence': sequence}
                else:
                    receipt = {'binding': binding.model_dump_json(),
                        'artifact_sequences': {artifact.id: sequence for artifact, _, sequence in outputs.values()}}
                    provenance = {'artifact_ids': list(receipt['artifact_sequences'])}
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, stage, json.dumps(receipt)))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'pipeline_materialized', task=task, recipe_id=recipe.id, stage=stage,
                    binding_id=binding.id, **provenance)
            return ()
        except (OSError, ValueError) as error:
            reason = getattr(error, 'code', 'pipeline_unavailable')
            return () if reason == 'pipeline_wait' else (reason,)
