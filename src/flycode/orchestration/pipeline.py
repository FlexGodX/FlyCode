"""Bounded runtime materialization delegated by exact, durable owner authority."""
import json
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from ..domain.orchestration import (
    IntegrationBinding,
    ReviewBinding,
    WorkerBinding,
    stage_binding,
)
from ..domain.pipeline import (
    DependencyPipelineRecipe,
    ParentPipelineRecipe,
    PipelineBranch,
    PipelineRecipe,
    RetryPolicy,
    TeamPipelineRecipe,
)
from ..domain.results import ReviewRecord
from ..verification.plan import contract_hash
from ..verification.snapshot import capture
from .completions import Completions
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
        self.integration = Integration(store, self.root)

    def get(self, scope, identity):
        row = self.store.db.execute('SELECT data FROM pipelines WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'pipeline')
        recipe = self.effective(self.store, row['data'])
        self.store.workflow(scope, recipe.workflow_id)
        if recipe.runtime_root != str(self.root):
            raise StoreError('pipeline_scope')
        return recipe

    @staticmethod
    def _recipe(raw):
        model = {2: TeamPipelineRecipe, 3: DependencyPipelineRecipe, 4: ParentPipelineRecipe}.get(json.loads(raw).get('version'), PipelineRecipe)
        return model.model_validate_json(raw)

    @staticmethod
    def effective(store, raw):
        from .replans import Replans

        return Replans.effective(store, raw)

    @staticmethod
    def receipts(store, recipe):
        from .replans import Replans

        return Replans.receipts(store, recipe)

    @staticmethod
    def stage(recipe, logical):
        number = getattr(recipe, 'graph_revision', 0)
        return f'g{number}:' + logical if number else logical

    def replan(self, scope, identity, **values):
        from .replans import Replans

        return Replans(self.store, self.root).approve(scope, identity, **values)

    @staticmethod
    def _branches(recipe):
        if isinstance(recipe, (TeamPipelineRecipe, DependencyPipelineRecipe, ParentPipelineRecipe)):
            return recipe.branches
        return (PipelineBranch(writer_id=recipe.writer_id, reviewer_id=recipe.reviewer_id,
            worker_binding_id=recipe.worker_binding_id, worker_binding_hash=recipe.worker_binding_hash),)

    @staticmethod
    def attempt_cap(recipe, task_id):
        if recipe.version == 4:
            for branch in recipe.branches:
                policy = recipe.retry_policies.get(branch.writer_id)
                if policy is not None:
                    if task_id == branch.writer_id:
                        return policy.writer_budget
                    if task_id == branch.reviewer_id:
                        return policy.reviewer_budget
        return None

    @staticmethod
    def _review_stage(recipe, branch):
        return 'review' if recipe.version == 1 else 'review:' + branch.reviewer_id

    def approve_team(self, scope, workflow_id, pairs, integrator_id, *, source,
                     expected_revisions, approval_hashes, key, reruns=0):
        return self._approve_many(scope, workflow_id, pairs, integrator_id, source=source,
            expected_revisions=expected_revisions, approval_hashes=approval_hashes, key=key, reruns=reruns)

    def approve_dag(self, scope, workflow_id, pairs, integrator_id, *, source,
                    expected_revisions, approval_hashes, key, reruns=0):
        return self._approve_many(scope, workflow_id, pairs, integrator_id, source=source,
            expected_revisions=expected_revisions, approval_hashes=approval_hashes, key=key, reruns=reruns, dependent=True)

    def approve_parent(self, scope, workflow_id, pairs, integrator_id, *, source,
                       expected_revisions, approval_hashes, key, preparation_steps=1, reruns=0, retry_policies=None):
        return self._approve_many(scope, workflow_id, pairs, integrator_id, source=source,
            expected_revisions=expected_revisions, approval_hashes=approval_hashes, key=key, reruns=reruns,
            dependent=True, parent=True, preparation_steps=preparation_steps, retry_policies=retry_policies)

    def _approve_many(self, scope, workflow_id, pairs, integrator_id, *, source,
                      expected_revisions, approval_hashes, key, reruns, dependent=False, parent=False, preparation_steps=1, retry_policies=None):
        store = self.store
        store._role(scope, ('owner',))
        store.workflow(scope, workflow_id)
        model = ParentPipelineRecipe if parent else DependencyPipelineRecipe if dependent else TeamPipelineRecipe
        request = {('parent_pipeline' if parent else 'dependency_pipeline' if dependent else 'team_pipeline'): workflow_id, 'pairs': pairs, 'integrator': integrator_id,
            'revisions': expected_revisions, 'approvals': approval_hashes, 'source': str(source),
            'root': str(self.root), 'reruns': reruns}
        policies = TypeAdapter(dict[str, RetryPolicy]).validate_python(retry_policies) if retry_policies is not None else {}
        if parent:
            request['preparation_steps'] = preparation_steps
            if policies:
                request['retry_policies'] = {key: value.model_dump(mode='json') for key, value in policies.items()}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return model.model_validate(saved)
            workflow, tasks, profiles, bindings = self._initial_many(scope, workflow_id, pairs,
                integrator_id, expected_revisions, dependent=dependent, parent=parent)
            if parent:
                root = tasks[workflow.root_task_id]
                if (type(preparation_steps) is not int or not 1 <= preparation_steps < min(
                        root.budget.model_calls, root.budget.tool_calls) or root.dependencies):
                    raise StoreError('parent_budget')
                if not set(policies) <= set(pairs):
                    raise StoreError('retry_scope')
                for identity, policy in policies.items():
                    count = policy.max_retries + 1 + int(identity == root.id)
                    writer, reviewer = tasks[identity], tasks[pairs[identity]]
                    if (count > workflow.budget.max_attempts or policy.writer_budget.model_calls < 1
                            or policy.writer_budget.tool_calls < 1 or policy.reviewer_budget.model_calls != 0
                            or policy.reviewer_budget.tool_calls < 1
                            or writer.budget.model_calls < count * policy.writer_budget.model_calls
                            or writer.budget.tool_calls < count * policy.writer_budget.tool_calls
                            or reviewer.budget.tool_calls < (policy.max_retries + 1) * policy.reviewer_budget.tool_calls
                            or (identity == root.id and preparation_steps > min(
                                policy.writer_budget.model_calls, policy.writer_budget.tool_calls))):
                        raise StoreError('retry_budget')
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
                return model.model_validate(saved)
            if self._initial_many(scope, workflow_id, pairs, integrator_id, expected_revisions,
                    dependent=dependent, parent=parent) != (workflow, tasks, profiles, bindings):
                raise StoreError('revision')
            recipe = model(id=uuid4().hex, owner_id=scope.actor_id, project_id=workflow.project_id,
                workflow_id=workflow.id, workflow_semantic_revision=workflow.semantic_revision, workflow_budget=workflow.budget,
                branches=tuple(PipelineBranch(writer_id=writer, reviewer_id=pairs[writer], worker_binding_id=bindings[writer].id,
                    worker_binding_hash=record_hash(bindings[writer])) for writer in sorted(pairs)),
                integrator_id=integrator_id, tasks=tasks, profiles=profiles, source_root=str(source),
                runtime_root=str(self.root), source_hash=first.source_hash, approval_hashes=approval_hashes,
                reruns=reruns, approval_sequence=0, created_at=store.clock(),
                **({'parent_id': workflow.root_task_id, 'preparation_steps': preparation_steps, 'retry_policies': policies} if parent else {}))
            store._event(scope, workflow, 'pipeline_approved', recipe_id=recipe.id)
            recipe = changed(recipe, approval_sequence=store.db.execute('SELECT MAX(sequence) FROM events').fetchone()[0])
            store.db.execute('INSERT INTO pipelines VALUES (?,?,?)', (recipe.id, workflow.id, recipe.model_dump_json()))
            aggregates = set(tasks) - {workflow.root_task_id, integrator_id, *pairs, *pairs.values()}
            for identity in (workflow.root_task_id, *sorted(aggregates), *(sorted(pairs) if not parent else ())):
                task = tasks[identity]
                if dependent and identity in pairs and task.dependencies:
                    continue
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

    def _initial_many(self, scope, workflow_id, pairs, integrator_id, revisions, *, dependent=False, parent=False):
        store = self.store
        workflow = store.workflow(scope, workflow_id)
        tasks = {item.id: item for item in store.tasks(scope, workflow_id)}
        ids = {workflow.root_task_id, integrator_id, *pairs, *pairs.values()}
        aggregates = self.integration.aggregate_tasks(scope, workflow.id, ids) if dependent else set()
        if (not (2 if parent else 1) <= len(pairs) <= (31 if dependent else 3)
                or len(ids) != 2 * len(pairs) + (1 if parent else 2)
                or (parent and workflow.root_task_id not in pairs)
                or set(tasks) != ids | aggregates or set(revisions) != set(tasks) or workflow.state != 'draft'):
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
            if dependent:
                store._validate_graph(scope, workflow, task)
            store._ready(task, workflow)
            if identity in aggregates:
                continue
            if identity == workflow.root_task_id and not parent:
                continue
            if ((task.parent_id not in {workflow.root_task_id, *aggregates} and identity != workflow.root_task_id)
                    or not task.allowed_profile_ids):
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
            dependencies = set(tasks[writer_id].dependencies)
            if ((dependencies and not dependent) or not dependencies <= set(pairs) or writer_id in dependencies
                    or tasks[reviewer_id].dependencies
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
        return self.receipts(self.store, recipe)

    @staticmethod
    def _receipt_binding(raw):
        return stage_binding(raw) if 'mode' in json.loads(raw) else WorkerBinding.model_validate_json(raw)

    @staticmethod
    def _task_blocked(recipe, tasks, identity, receipts=None):
        """A paused V3 branch fences new admissions in its dependent subgraph."""
        if recipe.version not in {3, 4}:
            return False
        branches = Pipelines._branches(recipe)
        if recipe.version == 4:
            receipts = receipts or {}
            waiting = 'wait:' + recipe.parent_id in receipts
            resumed = 'writer:' + recipe.parent_id in receipts
            parent_review = next(branch.reviewer_id for branch in branches if branch.writer_id == recipe.parent_id)
            if ((identity == recipe.parent_id and waiting and not resumed)
                    or (identity != recipe.parent_id and not waiting)
                    or (identity == parent_review and not resumed)):
                return True
        def paused_ancestor(identity):
            while identity is not None:
                if tasks[identity].paused:
                    return True
                identity = tasks[identity].parent_id
            return False
        blocked = {branch.writer_id for branch in branches
            if paused_ancestor(branch.writer_id) or paused_ancestor(branch.reviewer_id)}
        while True:
            following = {branch.writer_id for branch in branches if set(tasks[branch.writer_id].dependencies) & blocked}
            if following <= blocked:
                break
            blocked |= following
        return (paused_ancestor(identity) or (identity == recipe.integrator_id and bool(blocked))
            or any(branch.writer_id in blocked and identity in {branch.writer_id, branch.reviewer_id} for branch in branches))

    def _guard(self, scope, recipe):
        result = self.guard_metadata(self.store, scope, recipe)
        if any(not self.store._binding_current(expected) for expected in recipe.profiles.values()):
            raise StoreError('model_unavailable')
        return result

    @staticmethod
    def guard_metadata(store, scope, recipe):
        """Check delegated graph authority using stored models only."""
        if recipe.version == 4:
            row = store.db.execute('SELECT data FROM pipelines WHERE id=? AND workflow_id=?',
                (recipe.id, recipe.workflow_id)).fetchone()
            if row is None or Pipelines.effective(store, row['data']) != recipe:
                raise StoreError('pipeline_changed')
            if recipe.graph_revision:
                from .replans import Replans
                from .retries import Retries

                for record in Replans.records(store, row['data'])[1]:
                    for rejection in record.rejections:
                        Retries(store, recipe.runtime_root).metadata(scope, rejection)
        workflow = store.workflow(scope, recipe.workflow_id)
        if (workflow.semantic_revision != recipe.workflow_semantic_revision
                or workflow.budget != recipe.workflow_budget):
            raise StoreError('pipeline_generation')
        if workflow.state not in {'draft', 'running'} or store._counter('queue_paused'):
            raise StoreError('paused')
        tasks = {item.id: item for item in store.tasks(scope, workflow.id)}
        if set(tasks) != set(recipe.tasks):
            raise StoreError('pipeline_scope')
        receipts = Pipelines.receipts(store, recipe)
        bindings = [Pipelines._receipt_binding(value['binding']) for value in receipts.values() if 'binding' in value]
        by_id = {binding.task_id: binding for binding in bindings}
        resets = {}
        if recipe.version == 4:
            from .retries import Retries

            for checkpoint in sorted(Retries.checkpoints(receipts), key=lambda item: item.number):
                policy = recipe.retry_policies.get(checkpoint.task_id)
                branch = next((item for item in recipe.branches if item.writer_id == checkpoint.task_id), None)
                if (policy is None or branch is None or checkpoint.pipeline_id != recipe.id
                        or checkpoint.number > policy.max_retries or checkpoint.review_binding.task_id != branch.reviewer_id
                        or checkpoint.reset_semantic_revision != recipe.tasks[branch.reviewer_id].semantic_revision + 2 * checkpoint.number):
                    raise StoreError('retry_changed')
                Retries(store, recipe.runtime_root).metadata(scope, checkpoint)
                resets[branch.reviewer_id] = changed(recipe.tasks[branch.reviewer_id], semantic_revision=checkpoint.reset_semantic_revision)
        for identity, task in tasks.items():
            expected = by_id.get(identity, resets.get(identity, recipe.tasks[identity]))
            if (task.semantic_revision != expected.semantic_revision or task.contract_hash != expected.contract_hash
                    or (task.paused and (recipe.version not in {3, 4} or identity == workflow.root_task_id))
                    or task.archived or not required_task(task)
                    or task.permissions is None or not task.permissions.approved):
                raise StoreError('pipeline_changed')
            if identity in by_id:
                table = 'worker_bindings' if isinstance(by_id[identity], WorkerBinding) else 'review_bindings'
                row = store.db.execute(f'SELECT id FROM {table} WHERE task_id=? AND current=1', (identity,)).fetchone()
                if row is None or row['id'] != by_id[identity].id:
                    raise StoreError('binding_revision')
        for identity, expected in recipe.profiles.items():
            if store._profile(identity) != expected:
                raise StoreError('model_unavailable')
        for branch in Pipelines._branches(recipe):
            original = store._load('worker_bindings', branch.worker_binding_id, WorkerBinding)
            binding = by_id.get(branch.writer_id, original)
            row = store.db.execute('SELECT id,data FROM worker_bindings WHERE task_id=? AND current=1', (branch.writer_id,)).fetchone()
            if (record_hash(original) != branch.worker_binding_hash or not isinstance(binding, WorkerBinding)
                    or row is None or row['id'] != binding.id or record_hash(WorkerBinding.model_validate_json(row['data'])) != record_hash(binding)):
                raise StoreError('binding_revision')
            if binding.id != original.id:
                task = tasks[branch.writer_id]
                expected = changed(original, id=binding.id, revision=original.revision + 1, previous_id=original.id,
                    semantic_revision=recipe.tasks[task.id].semantic_revision + 1, contract_hash=task.contract_hash,
                    owner_id=recipe.owner_id, created_at=binding.created_at)
                receipt = receipts['writer:' + task.id]
                if recipe.version == 4 and task.id == recipe.parent_id:
                    from .continuations import Continuations

                    checkpoint = Continuations(store, recipe.runtime_root).checkpoint(scope, recipe)
                    if (checkpoint is None or receipt.get('checkpoint_id') != checkpoint[0].id
                            or task.dependencies != Continuations.children(recipe)):
                        raise StoreError('continuation_changed')
                if (recipe.version not in {3, 4} or binding != expected
                        or receipt['prerequisites'] != {key: value.model_dump(mode='json')
                            for key, value in store._prerequisites(scope, task).items()}):
                    raise StoreError('pipeline_changed')
            completion = receipts.get('completion:' + branch.writer_id)
            if completion is not None:
                proof = Completions(store, recipe.runtime_root).metadata(scope, completion['completion_id'])
                if (recipe.version not in {3, 4} or proof.task_id != branch.writer_id or proof.reviewer_task_id != branch.reviewer_id
                        or completion != {'completion_id': proof.id, 'completion_hash': record_hash(proof), 'artifact_id': proof.artifact_id}):
                    raise StoreError('pipeline_changed')
        return workflow, tasks, receipts

    def _artifact(self, scope, recipe, tasks, *, deep, writer_id=None):
        store = self.store
        task = tasks[writer_id or recipe.writer_id]
        if recipe.version in {3, 4} and task.state == 'succeeded' and task.completion_id is not None:
            service = Completions(store, self.root)
            proof = service.metadata(scope, task.completion_id)
            if deep and not service.current(scope, proof.id):
                raise StoreError('completion_changed')
            author = store._attempt(proof.attempt_id)
        elif task.state == 'awaiting_review' and task.current_attempt_id is not None:
            author = store._attempt(task.current_attempt_id)
        else:
            raise StoreError('pipeline_wait')
        completed = recipe.version in {3, 4} and task.state == author.state == 'succeeded' and task.fence == author.fence + 2
        awaiting = author.state == 'awaiting_review' and task.fence == author.fence + 1 and task.current_attempt_id == author.id
        if (not (completed or awaiting) or not author.stop_confirmed
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

    def _advance_dependencies(self, scope, recipe, tasks, receipts):
        store, completions = self.store, Completions(self.store, self.root)
        for branch in self._branches(recipe):
            stage = 'completion:' + branch.writer_id
            if stage in receipts or self._task_blocked(recipe, tasks, branch.writer_id, receipts):
                continue
            task, reviewer = tasks[branch.writer_id], tasks[branch.reviewer_id]
            if task.completion_id is not None:
                proof = completions.metadata(scope, task.completion_id)
                if not completions.current(scope, proof.id):
                    raise StoreError('completion_changed')
            elif task.state == 'awaiting_review' and reviewer.state == 'succeeded':
                rows = store.db.execute("""SELECT r.id FROM reviews r JOIN attempts a ON a.id=r.attempt_id
                    WHERE a.task_id=? AND a.state='succeeded' AND json_extract(a.data,'$.fence')=?""",
                    (reviewer.id, reviewer.fence - 1)).fetchall()
                if len(rows) != 1:
                    raise StoreError('review_changed')
                review = store._load('reviews', rows[0]['id'], ReviewRecord)
                proof = completions.complete(scope, review.artifact_id, review.id, expected_revision=task.revision,
                    key='pipeline-complete:' + recipe.id + ':' + task.id + ':' + review.id)
            else:
                continue
            if proof.task_id != branch.writer_id or proof.reviewer_task_id != branch.reviewer_id:
                raise StoreError('review_changed')
            with store.transaction():
                workflow, current, now = self._guard(scope, recipe)
                if stage in now:
                    return True
                if completions.metadata(scope, proof.id) != proof:
                    raise StoreError('revision')
                receipt = {'completion_id': proof.id, 'completion_hash': record_hash(proof), 'artifact_id': proof.artifact_id}
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, self.stage(recipe, stage), json.dumps(receipt)))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'pipeline_task_completed', task=current[task.id], recipe_id=recipe.id,
                    completion_id=proof.id, artifact_id=proof.artifact_id)
            return True
        for branch in self._branches(recipe):
            task, stage = tasks[branch.writer_id], 'writer:' + branch.writer_id
            if (stage in receipts or not task.dependencies or task.state != 'planned'
                    or self._task_blocked(recipe, tasks, task.id, receipts)):
                continue
            if any(tasks[identity].state != 'succeeded' for identity in task.dependencies):
                continue
            pins = store._prerequisites(scope, task)
            completions._input_evidence(scope, pins, {})
            if capture(Path(recipe.source_root)).subject_hash != recipe.source_hash:
                raise StoreError('snapshot_changed')
            original = store._load('worker_bindings', branch.worker_binding_id, WorkerBinding)
            ready = changed(task, state='ready', semantic_revision=task.semantic_revision + 1,
                revision=task.revision + 1, queued_at=store.clock(), updated_at=store.clock())
            binding = changed(original, id=uuid4().hex, revision=original.revision + 1, previous_id=original.id,
                semantic_revision=ready.semantic_revision, contract_hash=ready.contract_hash,
                owner_id=recipe.owner_id, created_at=store.clock())
            with store.transaction():
                workflow, current, now = self._guard(scope, recipe)
                if stage in now:
                    return True
                if current != tasks or receipts != now or store._prerequisites(scope, task) != pins:
                    raise StoreError('revision')
                store._ready(ready, workflow)
                store._validate_graph(scope, workflow, ready)
                store._save_task(ready)
                store.db.execute('UPDATE worker_bindings SET current=0 WHERE task_id=? AND current=1', (task.id,))
                store.db.execute('INSERT INTO worker_bindings VALUES (?,?,?,?)', (binding.id, task.id, 1, binding.model_dump_json()))
                receipt = {'binding': binding.model_dump_json(), 'prerequisites': {key: value.model_dump(mode='json') for key, value in pins.items()}}
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, self.stage(recipe, stage), json.dumps(receipt)))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'pipeline_materialized', task=ready, recipe_id=recipe.id, stage=stage, binding_id=binding.id)
            return True
        return False

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
            if recipe.version == 4:
                from .continuations import Continuations
                from .retries import Retries

                if Retries(store, self.root).advance(scope, self, recipe, tasks, receipts):
                    return ()
                if Continuations(store, self.root).advance(scope, self, recipe, tasks, receipts):
                    return ()
            if recipe.version in {3, 4} and self._advance_dependencies(scope, recipe, tasks, receipts):
                return ()
            branches, outputs = self._branches(recipe), {}
            for branch in branches:
                if self._task_blocked(recipe, tasks, branch.writer_id, receipts):
                    continue
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
                if recipe.version == 4:
                    from .retries import Retries

                    previous = max((item for item in Retries.checkpoints(receipts) if item.review_binding.task_id == task.id),
                        key=lambda item: item.number, default=None)
                    if previous is not None:
                        common.update(revision=previous.review_binding.revision + 1, previous_id=previous.review_binding.id)
                if recipe.version == 4 and recipe.graph_revision and 'previous_id' not in common:
                    row = store.db.execute('SELECT data FROM review_bindings WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task.id,)).fetchone()
                    if row is not None:
                        previous = self._receipt_binding(row['data'])
                        common.update(revision=previous.revision + 1, previous_id=previous.id)
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
                    row = store.db.execute("SELECT r.id FROM reviews r JOIN attempts a ON a.id=r.attempt_id "
                        "WHERE a.task_id=? AND a.state='succeeded' AND json_extract(a.data,'$.fence')=?",
                        (reviewer.id, reviewer.fence - 1)).fetchall()
                    if len(row) != 1:
                        raise StoreError('review_changed')
                    reviews.append(integrated.reviews.get(scope, row[0]['id']))
                expected = {**tasks, task.id: task}
                aggregates = integrated.aggregate_tasks(scope, workflow.id,
                    {workflow.root_task_id, *outputs, task.id}) if recipe.version in {3, 4} else set()
                binding = IntegrationBinding(**common, source_root=recipe.source_root, source_hash=recipe.source_hash,
                    artifact_hashes={artifact.id: artifact.patch_hash for artifact, _, _ in outputs.values()},
                    artifact_records={artifact.id: record_hash(artifact) for artifact, _, _ in outputs.values()},
                    review_ids=tuple(review.id for review in reviews), review_records={review.id: record_hash(review) for review in reviews},
                    author_attempts={writer: author.id for writer, (_, author, _) in outputs.items()},
                    author_completions=Completions(store, self.root).bindings(scope,
                        {writer: author.id for writer, (_, author, _) in outputs.items()}) if recipe.version in {3, 4} else {},
                    contracts={key: item.contract_hash for key, item in expected.items()},
                    semantic_revisions={key: item.semantic_revision for key, item in expected.items()},
                    approval_hashes={key: recipe.approval_hashes[key] for key in (workflow.root_task_id, *outputs, *sorted(aggregates), task.id)})
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
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, self.stage(recipe, stage), json.dumps(receipt)))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'pipeline_materialized', task=task, recipe_id=recipe.id, stage=stage,
                    binding_id=binding.id, **provenance)
            return ()
        except (OSError, ValueError) as error:
            reason = getattr(error, 'code', 'pipeline_unavailable')
            return () if reason == 'pipeline_wait' else (reason,)
