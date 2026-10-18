"""Exact owner approval for the existing journaled host integrator."""
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import IntegrationBinding
from ..domain.results import PatchArtifact, ReviewRecord
from ..verification.snapshot import capture, digest
from .integration import Integration
from .review_bindings import ReviewBindings
from .store import ACTIVE, StoreError, required_task


def record_hash(record):
    return digest(record.model_dump(mode='json', exclude_unset=isinstance(record, PatchArtifact)))


class IntegrationBindings:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root).resolve()
        self.reviews = ReviewBindings(store, root)

    def get(self, scope, task_id):
        self.store.task(scope, task_id)
        row = self.store.db.execute('SELECT data FROM review_bindings WHERE task_id=? AND current=1', (task_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'integration binding')
        return IntegrationBinding.model_validate_json(row['data'])

    def verify_metadata(self, scope, binding):
        from .completions import Completions

        store = self.store
        task, workflow = store.task(scope, binding.task_id), store.workflow(scope, binding.workflow_id)
        if not binding.matches(task, workflow):
            raise StoreError('execution_binding_stale')
        tasks = [item for item in store.tasks(scope, workflow.id) if required_task(item)]
        if ({item.id: item.contract_hash for item in tasks} != binding.contracts
                or {item.id: item.semantic_revision for item in tasks} != binding.semantic_revisions):
            raise StoreError('contract_changed')
        if any(item.paused or item.permissions is None or not item.permissions.approved for item in tasks):
            raise StoreError('completion_blocked')
        if any(item.state in ACTIVE and item.id != task.current_attempt_id for item in store.attempts(scope, workflow.id)):
            raise StoreError('incomplete_workflow')
        root = store.task(scope, workflow.root_task_id)
        if root.id not in binding.author_attempts and root.current_attempt_id is not None:
            raise StoreError('incomplete_workflow')
        completed = Completions(store, self.root).require_bound(scope, binding)
        for task_id, identity in binding.author_attempts.items():
            original, author = store.task(scope, task_id), store._attempt(identity)
            if task_id in completed and binding.previous_result_id is None:
                valid = original.current_attempt_id is None and original.state == author.state == 'succeeded'
            elif binding.previous_result_id is None:
                valid = (original.current_attempt_id == identity and original.state == author.state == 'awaiting_review'
                    and original.fence == author.fence + 1)
            else:
                valid = (original.current_attempt_id is None and author.state == 'succeeded'
                    and original.state in {'planned', 'ready', 'blocked', 'succeeded'})
            if not valid or not author.stop_confirmed:
                raise StoreError('fence')
        for identity, expected in binding.artifact_records.items():
            if record_hash(self.reviews.artifacts._record(scope, identity)) != expected:
                raise StoreError('artifact_integrity')
        for identity, expected in binding.review_records.items():
            review = store._load('reviews', identity, ReviewRecord)
            reviewer = store._attempt(review.reviewer_attempt_id)
            current = store.task(scope, reviewer.task_id)
            if (record_hash(review) != expected or reviewer.state != 'succeeded' or not reviewer.stop_confirmed
                    or current.semantic_revision != reviewer.semantic_revision or current.contract_hash != review.reviewer_contract_hash
                    or (current.state != 'succeeded' and not (binding.previous_result_id and current.state == 'blocked'))):
                raise StoreError('review_changed')
        return binding

    def verify(self, scope, binding):
        self.verify_metadata(scope, binding)
        service = Integration(self.store, self.root)
        if capture(Path(binding.source_root)).subject_hash != binding.source_hash:
            raise StoreError('snapshot_changed')
        service._reviewed(scope, binding, context=binding)
        service._lineage_guard(scope, binding, deep=True)
        for identity, approval in binding.approval_hashes.items():
            try:
                service.engine.oracles.resolve(approval, self.store.task(scope, identity).acceptance)
            except ValueError as error:
                raise StoreError('oracle_changed') from error
        return binding

    def bind(self, scope, task_id, artifact_ids, review_ids, *, approval_hashes, expected_revision, key,
             expected_binding_id=None, reruns=0, previous_result_id=None):
        from .completions import Completions

        store = self.store
        store._role(scope, ('owner',))
        store.task(scope, task_id)
        request = {'bind_integration': task_id, 'artifacts': tuple(artifact_ids), 'reviews': tuple(review_ids),
            'approvals': approval_hashes, 'revision': expected_revision, 'previous': expected_binding_id,
            'reruns': reruns, 'previous_result': previous_result_id, 'root': str(self.root)}
        with store.transaction():
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return IntegrationBinding.model_validate(saved)
            task, previous = self.reviews._can_bind(scope, task_id, expected_revision, expected_binding_id)
            workflow = store.workflow(scope, task.workflow_id)
        service = Integration(store, self.root)
        artifacts = [service.artifacts.get(scope, identity) for identity in artifact_ids]
        reviews = [service.reviews.get(scope, identity) for identity in review_ids]
        hashes = {item.id: item.patch_hash for item in artifacts}
        if (not artifacts or len(hashes) != len(artifacts) or len({item.task_id for item in artifacts}) != len(artifacts)
                or task.input_artifacts != hashes or any(item.workflow_id != workflow.id for item in artifacts)
                or len(set(review_ids)) != len(reviews) or len(reviews) != len(artifacts)
                or {item.artifact_id for item in reviews} != set(hashes)):
            raise StoreError('integration_scope')
        sources = {service.artifacts.workspaces.get(scope, item.attempt_id).source_root for item in artifacts}
        if len(sources) != 1:
            raise StoreError('integration_scope')
        source = Path(sources.pop())
        before = capture(source)
        if previous_result_id is None:
            if any(item.base.subject_hash != before.subject_hash for item in artifacts):
                raise StoreError('integration_conflict')
        else:
            old = service.get(scope, previous_result_id)
            if (old.state != 'invalidated' or old.workflow_id != workflow.id or old.source_root != str(source)
                    or old.artifact_hashes != hashes or old.candidate is None or before.files != old.candidate.files
                    or before.modes != old.candidate.modes or before.base != old.source_before.base
                    or not service._installed(scope, old) or set(review_ids) & set(old.review_ids)):
                raise StoreError('integration_conflict')
        resolved = service._resolution_inputs(scope, workflow.id, artifacts)
        author_attempts = {item.task_id: item.attempt_id for item in artifacts}
        required = set(author_attempts) | {workflow.root_task_id, task.id}
        required.update(service.aggregate_tasks(scope, workflow.id, required))
        required.update(set().union(*(service._requirements(scope, item) for item in resolved)))
        if set(approval_hashes) != required:
            raise StoreError('approval_scope')
        tasks = [item for item in store.tasks(scope, workflow.id) if required_task(item)]
        binding = IntegrationBinding(id=uuid4().hex, task_id=task.id, workflow_id=workflow.id, project_id=task.project_id,
            owner_id=scope.actor_id, revision=previous.revision + 1 if previous else 1, previous_id=previous.id if previous else None,
            semantic_revision=task.semantic_revision, workflow_semantic_revision=workflow.semantic_revision,
            contract_hash=task.contract_hash, runtime_root=str(self.root), source_root=str(source), source_hash=before.subject_hash,
            artifact_hashes=hashes, artifact_records={item.id: record_hash(item) for item in artifacts}, review_ids=tuple(review_ids),
            review_records={item.id: record_hash(item) for item in reviews}, author_attempts=author_attempts,
            author_completions=Completions(store, self.root).bindings(scope, author_attempts, previous_result_id),
            contracts={item.id: item.contract_hash for item in tasks}, semantic_revisions={item.id: item.semantic_revision for item in tasks},
            approval_hashes=approval_hashes, previous_result_id=previous_result_id, resolved_conflicts=tuple(item.id for item in resolved),
            reruns=reruns, created_at=store.clock())
        self.verify(scope, binding)
        with store.transaction():
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return IntegrationBinding.model_validate(saved)
            self.reviews._can_bind(scope, task_id, expected_revision, expected_binding_id)
            self.verify_metadata(scope, binding)
            store.db.execute('UPDATE review_bindings SET current=0 WHERE task_id=? AND current=1', (task.id,))
            store.db.execute('INSERT INTO review_bindings VALUES (?,?,?,?)', (binding.id, task.id, 1, binding.model_dump_json()))
            store._event(scope, workflow, 'integration_binding_created', task=task, binding=binding.model_dump(mode='json'))
            store._remember(scope, key, task.project_id, request_hash, binding)
            return binding
