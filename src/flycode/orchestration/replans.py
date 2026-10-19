"""Append-only owner graph authority; old generations cannot refund work."""

import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field, TypeAdapter

from ..domain.orchestration import WorkerBinding
from ..domain.pipeline import (
    BranchAddition,
    GraphInput,
    GraphRevisionReceipt,
    ParentPipelineRecipe,
    RetryCheckpoint,
)
from ..domain.results import ReviewRecord
from ..verification.snapshot import capture, digest
from .branching import Branching
from .completions import Completions
from .integration_bindings import record_hash
from .retries import Retries
from .store import ACTIVE, StoreError, changed


class Replans:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root).resolve()

    @staticmethod
    def records(store, raw):
        from .pipeline import Pipelines

        base = Pipelines._recipe(raw)
        rows = store.db.execute("SELECT stage,data FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'graph:%' ORDER BY stage", (base.id,)).fetchall()
        if not rows:
            return base, []
        if base.version != 4 or base.graph_revision or len(rows) > 2:
            raise StoreError('graph_changed')
        previous = hashlib.sha256(raw.encode()).hexdigest()
        records = []
        for number, row in enumerate(rows, 1):
            saved = GraphRevisionReceipt.model_validate_json(row['data'])
            recipe = saved.recipe
            event = store.db.execute("SELECT data FROM events WHERE workflow_id=? AND json_extract(data,'$.kind')='pipeline_replanned' "
                "AND json_extract(data,'$.data.recipe_id')=? AND json_extract(data,'$.data.number')=?", (base.workflow_id, base.id, number)).fetchall()
            if (row['stage'] != 'graph:' + str(number) or saved.number != number or recipe.graph_revision != number
                    or saved.base_hash != hashlib.sha256(raw.encode()).hexdigest() or saved.previous_hash != previous
                    or recipe.id != base.id or recipe.workflow_id != base.workflow_id or recipe.project_id != base.project_id
                    or recipe.runtime_root != base.runtime_root or saved.owner_id != recipe.owner_id
                    or len(event) != 1 or json.loads(event[0]['data'])['data'].get('receipt_hash') != record_hash(saved)):
                raise StoreError('graph_changed')
            Replans.carried(store, saved)
            records.append(saved)
            previous = record_hash(saved)
        return base, records

    @staticmethod
    def effective(store, raw):
        base, records = Replans.records(store, raw)
        return records[-1].recipe if records else base

    @staticmethod
    def carried(store, saved):
        if set(saved.carried) != set(saved.carry_hashes):
            raise StoreError('graph_changed')
        result = {}
        for logical, physical in saved.carried.items():
            row = store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (saved.recipe.id, physical)).fetchone()
            if row is None or digest(json.loads(row['data'])) != saved.carry_hashes[logical]:
                raise StoreError('graph_changed')
            result[logical] = json.loads(row['data'])
        return result

    @staticmethod
    def receipts(store, recipe):
        rows = store.db.execute('SELECT stage,data FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,)).fetchall()
        number = getattr(recipe, 'graph_revision', 0)
        if not number:
            return {row['stage']: json.loads(row['data']) for row in rows if not row['stage'].startswith(('graph:', 'g1:', 'g2:'))}
        row = next((row for row in rows if row['stage'] == 'graph:' + str(number)), None)
        if row is None:
            raise StoreError('graph_changed')
        saved = GraphRevisionReceipt.model_validate_json(row['data'])
        if saved.recipe != recipe:
            raise StoreError('graph_changed')
        result = Replans.carried(store, saved)
        prefix = f'g{number}:'
        for row in rows:
            if row['stage'].startswith(prefix + 'retired:'):
                result.pop(row['stage'][len(prefix + 'retired:'):], None)
        result.update({row['stage'][len(prefix):]: json.loads(row['data']) for row in rows
            if row['stage'].startswith(prefix) and not row['stage'].startswith(prefix + 'retired:')})
        return result

    def _guard(self, scope, service, identity, revisions, revision):
        store = self.store
        recipe = service.get(scope, identity)
        workflow, tasks, receipts = service.guard_metadata(store, scope, recipe)
        store._scope(scope, workflow.project_id, ('owner',))
        if recipe.version != 4 or recipe.graph_revision >= 2:
            raise StoreError('replan_limit')
        if workflow.revision != revision or set(revisions) != set(tasks) or any(task.revision != revisions[key] for key, task in tasks.items()):
            raise StoreError('revision')
        parent = tasks[recipe.parent_id]
        if (workflow.state != 'running' or parent.paused or any(task.paused for task in tasks.values())
                or parent.state != 'blocked' or 'writer:' + parent.id in receipts or 'integration' in receipts
                or 'wait:' + parent.id not in receipts):
            raise StoreError('replan_state')
        if workflow.deadline_at is None or store.clock() >= workflow.deadline_at or store.clock() < workflow.started_at:
            raise StoreError('wall_deadline')
        attempts = store.attempts(scope, workflow.id)
        if any(attempt.state in ACTIVE or not attempt.stop_confirmed for attempt in attempts):
            raise StoreError('stop_unconfirmed')
        if store.budget(scope, workflow.id).uncertain:
            raise StoreError('resource_busy')
        for attempt in attempts:
            Completions(store, self.root)._settled(attempt.id)
        return recipe, workflow, tasks, receipts, attempts

    def evidence(self, scope, saved, *, retired=True):
        if self.store.db.in_transaction:
            raise RuntimeError('Graph evidence cannot be inspected in a transaction')
        for rejection in saved.rejections:
            Retries(self.store, self.root).evidence(scope, rejection, retired=retired)

    def approve(self, scope, pipeline_id, *, changes, expected_revision, expected_revisions, key, new_branches=None):
        from .continuations import Continuations
        from .pipeline import Pipelines

        store, service = self.store, Pipelines(self.store, self.root)
        store._role(scope, ('owner',))
        additions = TypeAdapter(Annotated[tuple[BranchAddition, ...], Field(max_length=30)]).validate_json(json.dumps(new_branches if new_branches is not None else (),
            default=lambda value: value.model_dump(mode='json', warnings='error'), allow_nan=False))
        request = {'replan': pipeline_id, 'changes': changes, 'revision': expected_revision, 'revisions': expected_revisions, 'root': str(self.root)}
        if additions:
            request['new_branches'] = [item.model_dump(mode='json') for item in additions]
        with store.transaction():
            found, result, _ = store._replay(scope, key, request)
            if found:
                return ParentPipelineRecipe.model_validate(result)
            recipe, workflow, tasks, receipts, attempts = self._guard(scope, service, pipeline_id, expected_revisions, expected_revision)
        children = set(Continuations.children(recipe))
        if (not changes and not additions) or not set(changes) <= children:
            raise StoreError('replan_scope')
        branching = Branching(store, self.root)
        additions, new_profiles, templates = branching.prepare(scope, recipe, tasks, attempts, additions, service.reviews.oracles)
        references = children | {f'new:{number}' for number in range(len(additions))}
        updated, bindings, rejections = {}, {}, []
        altered = set(changes)
        for identity, values in changes.items():
            branch = next(branch for branch in recipe.branches if branch.writer_id == identity)
            task, reviewer = tasks[identity], tasks[branch.reviewer_id]
            if not values or not set(values) <= {'objective', 'dependencies'}:
                raise StoreError('replan_scope')
            if task.state in {'succeeded', 'cancelled'} or task.completion_id is not None or reviewer.state not in {'planned', 'failed'}:
                raise StoreError('replan_state')
            if any(identity in other.dependencies and other.id not in changes and other.state != 'planned' for other in tasks.values()):
                raise StoreError('replan_dependents')
            candidate = changed(task, **values)
            if (not candidate.objective.strip() or not set(candidate.dependencies) <= references or candidate == task):
                raise StoreError('replan_scope')
            for item in (task, reviewer):
                previous = [attempt for attempt in attempts if attempt.task_id == item.id]
                cap = store._remaining_reservation(workflow, item, store.budget(scope, workflow.id), attempts)
                if (len(previous) >= workflow.budget.max_attempts or not cap.tool_calls
                        or (item.kind != 'review' and not cap.model_calls)):
                    raise StoreError('attempt_limit')
            if task.current_attempt_id is not None:
                artifact, author, sequence = service._artifact(scope, recipe, tasks, deep=True, writer_id=task.id)
                if reviewer.state != 'failed':
                    raise StoreError('replan_state')
                rows = store.db.execute("SELECT id FROM reviews WHERE attempt_id IN (SELECT id FROM attempts WHERE task_id=?) "
                    "AND json_extract(data,'$.reviewer_fence')=?", (reviewer.id, reviewer.fence - 1)).fetchall()
                if len(rows) != 1:
                    raise StoreError('review_changed')
                review = store._load('reviews', rows[0]['id'], ReviewRecord)
                if review.artifact_id != artifact.id or review.reviewer_attempt_id not in {attempt.id for attempt in attempts}:
                    raise StoreError('review_changed')
                binding = service._receipt_binding(receipts[service._review_stage(recipe, branch)]['binding'])
                rejections.append(RetryCheckpoint(id=uuid4().hex, pipeline_id=recipe.id, task_id=task.id, number=1,
                    artifact_id=artifact.id, artifact_hash=record_hash(artifact), review_id=review.id, review_hash=record_hash(review),
                    author_attempt_id=author.id, author_fence=author.fence, reviewer_attempt_id=review.reviewer_attempt_id,
                    review_binding=binding, artifact_sequence=sequence, reset_semantic_revision=reviewer.semantic_revision + 1,
                    created_at=store.clock()))
            elif task.state not in {'planned', 'ready'}:
                raise StoreError('replan_state')
            ready = changed(candidate, state='planned' if candidate.dependencies else 'ready', semantic_revision=task.semantic_revision + 1,
                revision=task.revision + 1, blocked_reason=None, current_attempt_id=None, fence=task.fence + 1,
                input_artifacts={}, queued_at=store.clock(), updated_at=store.clock())
            reset = changed(reviewer, state='planned', input_artifacts={}, semantic_revision=reviewer.semantic_revision + 1,
                revision=reviewer.revision + 1, blocked_reason=None, updated_at=store.clock())
            original = store._load('worker_bindings', branch.worker_binding_id, WorkerBinding)
            row = store.db.execute('SELECT data FROM worker_bindings WHERE task_id=? AND current=1', (task.id,)).fetchone()
            previous = WorkerBinding.model_validate_json(row['data']) if row is not None else original
            bindings[identity] = changed(previous, id=uuid4().hex, previous_id=previous.id, revision=previous.revision + 1,
                semantic_revision=ready.semantic_revision, contract_hash=ready.contract_hash, owner_id=scope.actor_id, created_at=store.clock())
            updated.update({identity: ready, reviewer.id: reset})
            altered.add(reviewer.id)
        carry = {logical: value for logical, value in receipts.items() if not any(
            logical in {'writer:' + identity, 'review:' + identity, 'completion:' + identity} or logical.startswith('retry:' + identity + ':')
            for identity in altered)}
        origins = {}
        if recipe.graph_revision:
            row = store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, 'graph:' + str(recipe.graph_revision))).fetchone()
            origins = GraphRevisionReceipt.model_validate_json(row['data']).carried
        carried = {}
        for logical in carry:
            physical = service.stage(recipe, logical)
            present = store.db.execute('SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, physical)).fetchone()
            carried[logical] = physical if present is not None else origins[logical]
        branches = tuple(changed(branch, worker_binding_id=bindings[branch.writer_id].id,
            worker_binding_hash=record_hash(bindings[branch.writer_id])) if branch.writer_id in bindings else branch for branch in recipe.branches)
        effective = changed(recipe, graph_revision=recipe.graph_revision + 1, owner_id=scope.actor_id,
            tasks={**recipe.tasks, **updated}, branches=branches)
        raw = store.db.execute('SELECT data FROM pipelines WHERE id=?', (recipe.id,)).fetchone()['data']
        _, records = self.records(store, raw)
        base_hash = hashlib.sha256(raw.encode()).hexdigest()
        saved = GraphRevisionReceipt(number=effective.graph_revision, base_hash=base_hash,
            previous_hash=record_hash(records[-1]) if records else base_hash, owner_id=scope.actor_id, recipe=effective,
            carried=carried, carry_hashes={logical: digest(value) for logical, value in carry.items()},
            rejections=tuple(rejections), created_at=store.clock())
        if any(not store._binding_current(profile) for profile in recipe.profiles.values()):
            raise StoreError('model_unavailable')
        self.evidence(scope, saved, retired=False)
        Continuations(store, self.root).checkpoint(scope, recipe, deep=True)
        for branch in recipe.branches:
            proof = tasks[branch.writer_id].completion_id
            if proof is not None and not Completions(store, self.root).current(scope, proof):
                raise StoreError('completion_changed')
        if capture(Path(recipe.source_root)).subject_hash != recipe.source_hash:
            raise StoreError('snapshot_changed')
        for identity, approval in recipe.approval_hashes.items():
            service.reviews.oracles.resolve(approval, tasks[identity].acceptance)
        with store.transaction():
            store._host_sql()
            found, result, request_hash = store._replay(scope, key, request)
            if found:
                return ParentPipelineRecipe.model_validate(result)
            current = self._guard(scope, service, recipe.id, expected_revisions, expected_revision)
            if current != (recipe, workflow, tasks, receipts, attempts):
                raise StoreError('revision')
            for rejection in saved.rejections:
                Retries(store, self.root).metadata(scope, rejection, retired=False)
            branching.current(new_profiles, templates)
            added, new_bindings, approvals, new_pairs, aliases = branching.allocate(scope, recipe, workflow, additions, templates)
            for identity, task in tuple(updated.items()):
                resolved = changed(task, dependencies=tuple(aliases.get(dependency, dependency) for dependency in task.dependencies))
                updated[identity] = resolved
                if identity in bindings:
                    bindings[identity] = changed(bindings[identity], contract_hash=resolved.contract_hash)
            updated.update(added)
            bindings.update(new_bindings)
            if new_pairs:
                integrator = tasks[recipe.integrator_id]
                updated[integrator.id] = changed(integrator, dependencies=(*integrator.dependencies, *(pair.reviewer_id for pair in new_pairs)),
                    semantic_revision=integrator.semantic_revision + 1, revision=integrator.revision + 1, updated_at=store.clock())
            branches = tuple(changed(branch, worker_binding_id=bindings[branch.writer_id].id,
                worker_binding_hash=record_hash(bindings[branch.writer_id])) if branch.writer_id in bindings else branch for branch in branches) + new_pairs
            effective = changed(effective, tasks={**recipe.tasks, **updated}, branches=branches,
                profiles={**recipe.profiles, **new_profiles}, approval_hashes={**recipe.approval_hashes, **approvals})
            saved = changed(saved, recipe=effective)
            for identity, task in updated.items():
                if identity in tasks and tasks[identity].current_attempt_id is not None:
                    store._stop(tasks[identity])
                store._ready(task, workflow)
                store._save_task(task)
            for task in updated.values():
                store._validate_graph(scope, workflow, task)
            for identity, binding in bindings.items():
                store.db.execute('UPDATE worker_bindings SET current=0 WHERE task_id=?', (identity,))
                store.db.execute('INSERT INTO worker_bindings VALUES (?,?,?,?)', (binding.id, identity, 1, binding.model_dump_json()))
            for branch in branches:
                if branch.writer_id in changes:
                    store.db.execute('UPDATE review_bindings SET current=0 WHERE task_id=?', (branch.reviewer_id,))
            for pair in new_pairs:
                for identity in (pair.writer_id, pair.reviewer_id):
                    store._event(scope, workflow, 'task_created', task=updated[identity], revision=updated[identity].revision)
                store._event(scope, workflow, 'worker_binding_created', task=updated[pair.writer_id], binding=bindings[pair.writer_id].model_dump(mode='json'))
            store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, 'graph:' + str(saved.number), saved.model_dump_json()))
            store._save_workflow(changed(workflow, revision=workflow.revision + 1))
            store._event(scope, workflow, 'pipeline_replanned', recipe_id=recipe.id, number=saved.number,
                receipt_hash=record_hash(saved), previous_hash=saved.previous_hash, task_ids=sorted(changes))
            service.guard_metadata(store, scope, effective)
            store._remember(scope, key, workflow.project_id, request_hash, effective)
        return effective

    def input(self, scope, attempt, *, deep=False):
        self.store.task(scope, attempt.task_id)
        row = self.store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (attempt.workflow_id,)).fetchone()
        if row is None:
            return None
        _, records = self.records(self.store, row['data'])
        if not records:
            return None
        saved = records[-1]
        if deep:
            for record in records:
                self.evidence(scope, record)
        return GraphInput(pipeline_id=saved.recipe.id, number=saved.number, receipt_hash=record_hash(saved))

    def context(self, scope, attempt):
        pin = self.input(scope, attempt, deep=True)
        return '\nOwner-approved graph revision (no additional authority): ' + pin.model_dump_json() if pin is not None else ''

    def require_artifact(self, scope, artifact):
        pin = artifact.graph_input
        row = self.store.db.execute('SELECT data FROM pipelines WHERE id=?', (pin.pipeline_id,)).fetchone()
        if row is None:
            raise StoreError('graph_changed')
        base, records = self.records(self.store, row['data'])
        if (base.workflow_id != artifact.workflow_id or base.project_id != artifact.project_id or len(records) < pin.number
                or record_hash(records[pin.number - 1]) != pin.receipt_hash):
            raise StoreError('graph_changed')
        for saved in records[:pin.number]:
            self.evidence(scope, saved)
