"""Checked task results unlock dependencies; workflow integration remains separate."""

import stat
from itertools import chain
from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkerRecord
from ..domain.results import (
    CompletionInput,
    IntegrationRecord,
    PatchArtifact,
    ReviewExecution,
    ReviewRecord,
    TaskCompletion,
)
from ..domain.verification import VerificationPlan
from ..verification.evidence import read_regular
from ..verification.plan import contract_hash
from ..verification.snapshot import MAX_FILES, capture, digest
from .artifacts import Artifacts
from .integration import registered_source
from .review import Reviews
from .sources import Sources
from .store import StoreError, changed, required_task, structural_aggregate
from .verification import _succeed, accepted


def _hash(record):
    return digest(record.model_dump(mode='json', exclude_unset=isinstance(record, PatchArtifact)))


class Completions:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise StoreError('completion_scope')

    def get(self, scope, identity):
        record = self.store._load('task_completions', identity, TaskCompletion)
        self.store.task(scope, record.task_id)
        if record.runtime_root.path != str(self.root):
            raise StoreError('completion_scope')
        return record

    def _review(self, scope, identity):
        store = self.store
        review = store._load('reviews', identity, ReviewRecord)
        checker = store._attempt(review.reviewer_attempt_id)
        task = store.task(scope, checker.task_id)
        artifact = store._load('artifacts', review.artifact_id, PatchArtifact)
        author = store._attempt(artifact.attempt_id)
        original = store.task(scope, artifact.task_id)
        execution = store._load('review_executions', checker.id, ReviewExecution)
        if (review.status != 'approved' or review.patch_hash != artifact.patch_hash
                or checker.instance_id == author.instance_id or checker.profile_snapshot.role != 'reviewer'
                or task.kind != 'review'
                or task.state != 'succeeded' or checker.state != 'succeeded' or not checker.stop_confirmed
                or task.current_attempt_id is not None or task.fence != checker.fence + 1
                or checker.fence != review.reviewer_fence or checker.semantic_revision != task.semantic_revision
                or checker.contract_hash != task.contract_hash or review.reviewer_contract_hash != task.contract_hash
                or task.workflow_id != artifact.workflow_id or task.project_id != artifact.project_id
                or task.input_artifacts.get(artifact.id) != artifact.patch_hash
                or original.acceptance is None or task.acceptance is None
                or contract_hash(original.acceptance) != contract_hash(task.acceptance)
                or execution.runtime_root != str(self.root) or execution.state != 'completed' or execution.review_id != review.id
                or execution.attempt_id != checker.id or execution.task_id != task.id
                or execution.project_id != task.project_id or execution.workflow_id != task.workflow_id
                or execution.fence != review.reviewer_fence or execution.artifact_id != artifact.id
                or execution.patch_hash != review.patch_hash or execution.approval_hash != review.approval_hash
                or execution.reruns != review.reruns):
            raise StoreError('completion_review')
        self._settled(checker.id)
        return review, task, checker, artifact

    def _settled(self, attempt_id):
        for table in ('model_calls', 'tool_calls'):
            if self.store.db.execute(f"SELECT 1 FROM {table} WHERE attempt_id=? AND state IN ('running','uncertain')", (attempt_id,)).fetchone():
                raise StoreError('resource_busy')
        row = self.store.db.execute('SELECT uncertain FROM reservations WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None or row['uncertain']:
            raise StoreError('uncertain')

    def _facts(self, scope, artifact_id, review_id):
        store = self.store
        review, reviewer, checker, artifact = self._review(scope, review_id)
        if artifact.id != artifact_id:
            raise StoreError('completion_review')
        task = store.task(scope, artifact.task_id)
        workflow = store.workflow(scope, artifact.workflow_id)
        author = store._attempt(artifact.attempt_id)
        row = store.db.execute('SELECT data FROM workers WHERE attempt_id=?', (author.id,)).fetchone()
        if row is None:
            raise StoreError('completion_worker')
        worker = WorkerRecord.model_validate_json(row['data'])
        if (task.kind == 'review' or not required_task(task) or author.task_id != task.id
                or task.workflow_id != workflow.id or task.project_id != artifact.project_id
                or author.workflow_id != workflow.id or artifact.fence != author.fence
                or author.contract_hash != task.contract_hash or artifact.contract_hash != task.contract_hash
                or author.semantic_revision != task.semantic_revision or artifact.semantic_revision != task.semantic_revision
                or artifact.prerequisites != author.prerequisites
                or not author.stop_confirmed or not worker.stop_confirmed or worker.error is not None
                or worker.fence != author.fence or worker.result_hash != artifact.result_hash
                or task.permissions is None or not task.permissions.approved or reviewer.permissions is None or not reviewer.permissions.approved
                or store._project_revoked(task.project_id)):
            raise StoreError('completion_changed')
        self._settled(author.id)
        return artifact, review, task, author, reviewer, checker, workflow

    def _inputs(self, scope, task, cache, visiting):
        result = {}
        for identity in task.dependencies:
            dependency = self.store.task(scope, identity)
            if dependency.workflow_id != task.workflow_id or dependency.state != 'succeeded' or not required_task(dependency):
                raise StoreError('completion_dependency')
            if dependency.kind == 'review':
                rows = self.store.db.execute('SELECT r.id FROM reviews r JOIN attempts a ON a.id=r.attempt_id '
                    "WHERE a.task_id=? AND a.state='succeeded' AND json_extract(a.data,'$.fence')=?",
                    (identity, dependency.fence - 1)).fetchall()
                candidates = [self._review(scope, row['id'])[0] for row in rows]
                if len(candidates) != 1:
                    raise StoreError('completion_dependency')
                record = candidates[0]
            else:
                if dependency.completion_id is None:
                    raise StoreError('completion_dependency')
                record = self.metadata(scope, dependency.completion_id, _cache=cache, _visiting=visiting)
            result[identity] = CompletionInput(id=record.id, sha256=_hash(record))
        return result

    def _review_evidence(self, scope, identity):
        review, task, _, artifact = self._review(scope, identity)
        artifacts, reviews = Artifacts(self.store, self.root), Reviews(self.store, self.root)
        if artifacts.get(scope, artifact.id) != artifact or not accepted(reviews.engine, review.verdict,
                artifacts.directory / artifact.id / 'target', task.acceptance):
            raise StoreError('completion_evidence')
        plan = VerificationPlan.model_validate_json(read_regular(reviews.engine.data_dir / review.verdict.evidence_id / 'plan.json'))
        if digest(plan.model_dump(mode='json')) != review.verdict.plan_hash or plan.approval_hash != review.approval_hash or plan.reruns != review.reruns:
            raise StoreError('completion_evidence')

    def _input_evidence(self, scope, prerequisites, cache):
        for task_id, reference in prerequisites.items():
            if self.store.task(scope, task_id).kind == 'review':
                self._review_evidence(scope, reference.id)
            elif not self._current(scope, reference.id, cache):
                raise StoreError('completion_dependency')

    def _invalidated(self, record):
        return self.store.db.execute("""SELECT 1 FROM events WHERE workflow_id=?
            AND json_extract(data,'$.kind')='task_completion_revoked'
            AND json_extract(data,'$.data.completion_id')=? LIMIT 1""", (record.workflow_id, record.id)).fetchone() is not None

    def _evidence_pins(self, scope, review_id, prerequisites):
        """Keep every inspected evidence tree stable across prerequisite reads."""
        reviews, pending, seen = {review_id}, list(prerequisites.items()), set()
        while pending:
            task_id, reference = pending.pop()
            if self.store.task(scope, task_id).kind == 'review':
                reviews.add(reference.id)
            elif reference.id not in seen:
                seen.add(reference.id)
                record = self.metadata(scope, reference.id)
                reviews.add(record.review_id)
                pending.extend(record.prerequisites.items())
        directories = set()
        for identity in reviews:
            review, _, _, artifact = self._review(scope, identity)
            directories.update((self.root / 'artifacts' / artifact.id, self.root / 'verification' / review.verdict.evidence_id))
        pins = {}
        try:
            for directory in sorted(directories):
                for number, path in enumerate(chain((directory,), directory.rglob('*'))):
                    info = path.lstat()
                    if number >= MAX_FILES * 8 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise StoreError('completion_evidence')
                    pins[str(path)] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        except OSError as error:
            raise StoreError('completion_evidence') from error
        return pins

    def metadata(self, scope, identity, *, _cache=None, _visiting=frozenset()):
        if identity in _visiting or len(_visiting) >= 64:
            raise StoreError('completion_dependency')
        cache = {} if _cache is None else _cache
        if identity in cache:
            return cache[identity]
        record = self.get(scope, identity)
        artifact, review, task, author, reviewer, _, _ = self._facts(scope, record.artifact_id, record.review_id)
        if (self._invalidated(record) or task.state != 'succeeded' or task.current_attempt_id is not None or task.completion_id != record.id
                or author.state != 'succeeded' or task.fence != author.fence + 2
                or record.attempt_id != author.id or record.fence != author.fence
                or record.project_id != task.project_id or record.workflow_id != task.workflow_id
                or record.semantic_revision != task.semantic_revision or record.contract_hash != task.contract_hash
                or record.artifact_hash != _hash(artifact) or record.review_hash != _hash(review)
                or record.reviewer_task_id != reviewer.id or record.reviewer_semantic_revision != reviewer.semantic_revision
                or record.reviewer_contract_hash != reviewer.contract_hash
                or record.source_hash != artifact.base.subject_hash
                or record.prerequisites != author.prerequisites
                or record.prerequisites != self._inputs(scope, task, cache, _visiting | {identity})):
            raise StoreError('completion_changed')
        cache[identity] = record
        return record

    def current(self, scope, identity):
        if self.store.db.in_transaction:
            raise RuntimeError('Completion evidence inspection cannot run in a transaction')
        return self._current(scope, identity, {})

    def bindings(self, scope, authors, previous_result_id=None):
        if previous_result_id is not None:
            previous = self.store._load('integrations', previous_result_id, IntegrationRecord)
            self.store.workflow(scope, previous.workflow_id)
            if previous.author_attempts != authors:
                raise StoreError('completion_changed')
            return previous.author_completions
        return {identity: CompletionInput(id=record.id, sha256=_hash(record))
            for identity in authors if (completion := self.store.task(scope, identity).completion_id) is not None
            for record in (self.metadata(scope, completion),)}

    def lineage(self, scope, record):
        covered, references = set(record.author_attempts), dict(record.author_completions)
        pending, seen = list(record.resolved_conflicts), set()
        while pending:
            identity = pending.pop()
            if identity in seen or len(seen) >= 64:
                raise StoreError('resolution_scope')
            seen.add(identity)
            previous = self.store._load('integrations', identity, IntegrationRecord)
            if (previous.workflow_id != record.workflow_id or previous.source_root != record.source_root
                    or previous.runtime_root != str(self.root) or previous.state != 'conflict'):
                raise StoreError('resolution_scope')
            covered.update(previous.author_attempts)
            for task_id, reference in previous.author_completions.items():
                if task_id in references and references[task_id] != reference:
                    raise StoreError('completion_changed')
                references[task_id] = reference
            pending.extend(previous.resolved_conflicts)
        return covered, references

    def require_bound(self, scope, record, *, deep=False, coverage=True, context=None):
        """An integrator retains original-source proof while applying its checked delta."""
        covered, _ = self.lineage(scope, record)
        workflow = self.store.workflow(scope, record.workflow_id)
        tasks = self.store.tasks(scope, workflow.id)
        attempts = self.store.attempts(scope, workflow.id)
        aggregates = {task.id for task in tasks if structural_aggregate(task, tasks, attempts)
            and task.id in record.approval_hashes and record.contracts.get(task.id) == task.contract_hash
            and record.semantic_revisions.get(task.id) == task.semantic_revision}
        if coverage and any(task.kind != 'review' and required_task(task) and task.id not in covered
                and task.id not in aggregates
                and (task.id != workflow.root_task_id or task.completion_id is not None)
                for task in tasks):
            raise StoreError('incomplete_workflow')
        if record.previous_result_id is not None:
            return self._historical_bound(scope, record, deep=deep)
        if context is not None and context.previous_result_id is not None:
            self._historical_bound(scope, context, deep=False)
            _, references = self.lineage(scope, context)
            if (record.workflow_id != context.workflow_id or record.source_root != context.source_root
                    or record.runtime_root != str(self.root)
                    or any(references.get(identity) != reference for identity, reference in record.author_completions.items())):
                raise StoreError('completion_changed')
            previous = self.store._load('integrations', context.previous_result_id, IntegrationRecord)
            pending, seen = list(previous.resolved_conflicts), set()
            while pending:
                identity = pending.pop()
                if identity in seen or len(seen) >= 64:
                    raise StoreError('resolution_scope')
                seen.add(identity)
                conflict = self.store._load('integrations', identity, IntegrationRecord)
                if identity == record.id and conflict == record:
                    return self._retained_receipts(scope, record, record, deep=deep, context=context)
                pending.extend(conflict.resolved_conflicts)
            raise StoreError('resolution_scope')
        expected = self.bindings(scope, record.author_attempts)
        if record.author_completions != expected:
            raise StoreError('completion_changed')
        source_hash = record.source_before.subject_hash if hasattr(record, 'source_before') else record.source_hash
        records = []
        for task_id, reference in expected.items():
            proof = self.metadata(scope, reference.id)
            artifact = self.store._load('artifacts', proof.artifact_id, PatchArtifact)
            if (proof.attempt_id != record.author_attempts[task_id]
                    or record.artifact_hashes.get(proof.artifact_id) != artifact.patch_hash
                    or proof.review_id not in record.review_ids or proof.source_hash != source_hash
                    or proof.source_root.path != record.source_root):
                raise StoreError('completion_changed')
            records.append(proof)
        if deep:
            if self.store.db.in_transaction:
                raise RuntimeError('Bound completion evidence inspection cannot run in a transaction')
            from .composition import Compositions

            closure = Compositions(self.store, self.root).records(scope, expected)
            sources = Sources(self.store)
            for proof in closure:
                pins = self._evidence_pins(scope, proof.review_id, proof.prerequisites)
                if (sources.prepare(self.root) != proof.runtime_root
                        or sources.prepare(Path(record.source_root)) != proof.source_root):
                    raise StoreError('completion_changed')
                self._review_evidence(scope, proof.review_id)
                if self._evidence_pins(scope, proof.review_id, proof.prerequisites) != pins:
                    raise StoreError('completion_changed')
            if self.bindings(scope, record.author_attempts) != expected:
                raise StoreError('completion_changed')
        return {proof.task_id for proof in records}

    def _historical_bound(self, scope, record, *, deep):
        previous = self.store._load('integrations', record.previous_result_id, IntegrationRecord)
        if (previous.state != 'invalidated' or previous.workflow_id != record.workflow_id
                or previous.runtime_root != str(self.root) or previous.source_root != record.source_root
                or previous.author_attempts != record.author_attempts or previous.artifact_hashes != record.artifact_hashes
                or previous.author_completions != record.author_completions
                or previous.resolved_conflicts != record.resolved_conflicts or set(previous.review_ids) & set(record.review_ids)):
            raise StoreError('completion_changed')
        origin, seen = previous, {previous.id}
        while origin.previous_result_id is not None:
            origin = self.store._load('integrations', origin.previous_result_id, IntegrationRecord)
            if (origin.id in seen or len(seen) >= 64 or origin.workflow_id != record.workflow_id
                    or origin.source_root != record.source_root or origin.author_completions != record.author_completions
                    or origin.artifact_hashes != record.artifact_hashes or origin.author_attempts != record.author_attempts
                    or origin.resolved_conflicts != record.resolved_conflicts):
                raise StoreError('completion_changed')
            seen.add(origin.id)
        return self._retained_receipts(scope, record, origin, deep=deep)

    def _retained_receipts(self, scope, record, origin, *, deep, context=None):
        context = record if context is None else context
        for task_id, reference in record.author_completions.items():
            proof = self.get(scope, reference.id)
            artifact = self.store._load('artifacts', proof.artifact_id, PatchArtifact)
            review = self.store._load('reviews', proof.review_id, ReviewRecord)
            task, author = self.store.task(scope, task_id), self.store._attempt(proof.attempt_id)
            if (reference.sha256 != _hash(proof) or proof.task_id != task_id or proof.attempt_id != record.author_attempts.get(task_id)
                    or proof.source_hash != origin.source_before.subject_hash or proof.source_root.path != record.source_root
                    or proof.artifact_hash != _hash(artifact) or proof.review_hash != _hash(review)
                    or origin.artifact_hashes.get(artifact.id) != artifact.patch_hash or proof.review_id not in origin.review_ids
                    or origin.contracts.get(task_id) != proof.contract_hash or origin.semantic_revisions.get(task_id) != proof.semantic_revision
                    or task.contract_hash != context.contracts.get(task_id)
                    or task.semantic_revision != context.semantic_revisions.get(task_id)
                    or author.task_id != task_id or author.workflow_id != proof.workflow_id
                    or author.contract_hash != proof.contract_hash or author.semantic_revision != proof.semantic_revision
                    or author.fence != proof.fence or task.project_id != proof.project_id
                    or author.state != 'succeeded' or not author.stop_confirmed or task.current_attempt_id is not None
                    or task.fence != author.fence + 2 or proof.prerequisites != author.prerequisites
                    or artifact.prerequisites != proof.prerequisites):
                raise StoreError('completion_changed')
            if deep:
                if self.store.db.in_transaction:
                    raise RuntimeError('Historical evidence inspection cannot run in a transaction')
                sources = Sources(self.store)
                if (sources.prepare(self.root) != proof.runtime_root
                        or sources.prepare(Path(record.source_root)) != proof.source_root
                        or Artifacts(self.store, self.root).get(scope, artifact.id) != artifact):
                    raise StoreError('completion_changed')
        return set(record.author_completions)

    def _current(self, scope, identity, cache):
        if identity in cache:
            return cache[identity]
        try:
            record = self.metadata(scope, identity)
            sources = Sources(self.store)
            if sources.prepare(self.root) != record.runtime_root or sources.prepare(Path(record.source_root.path)) != record.source_root:
                return False
            pins = self._evidence_pins(scope, record.review_id, record.prerequisites)
            registered_source(self.store, record.project_id, record.source_root.path)
            if capture(Path(record.source_root.path)).subject_hash != record.source_hash:
                return (self._installed_context(scope, record)
                    and sources.prepare(self.root) == record.runtime_root
                    and sources.prepare(Path(record.source_root.path)) == record.source_root
                    and self._evidence_pins(scope, record.review_id, record.prerequisites) == pins
                    and self.metadata(scope, identity) == record)
            self._review_evidence(scope, record.review_id)
            self._input_evidence(scope, record.prerequisites, cache)
            result = (capture(Path(record.source_root.path)).subject_hash == record.source_hash
                and sources.prepare(self.root) == record.runtime_root
                and sources.prepare(Path(record.source_root.path)) == record.source_root
                and self._evidence_pins(scope, record.review_id, record.prerequisites) == pins
                and self.metadata(scope, identity) == record)
            cache[identity] = result
            return result
        except (OSError, ValueError):
            return False

    def _installed_context(self, scope, proof):
        from .integration import Integration

        service = Integration(self.store, self.root)
        reference = CompletionInput(id=proof.id, sha256=_hash(proof))
        task = self.store.task(scope, proof.task_id)
        if task.result_id is not None:
            record = service.get(scope, task.result_id)
            return self.lineage(scope, record)[1].get(task.id) == reference and service.current(scope, record.id)
        for record in self._applying(scope, proof):
            snapshot = capture(Path(record.source_root))
            if (service._installed(scope, record) and snapshot.files == record.candidate.files
                    and snapshot.modes == record.candidate.modes and snapshot.base == record.source_before.base
                    and service.get(scope, record.id) == record):
                return True
        return False

    def _applying(self, scope, proof):
        from .integration import Integration

        service = Integration(self.store, self.root)
        reference = CompletionInput(id=proof.id, sha256=_hash(proof))
        rows = self.store.db.execute("SELECT data FROM integrations WHERE workflow_id=? AND state='applying'", (proof.workflow_id,)).fetchall()
        for row in rows:
            record = IntegrationRecord.model_validate_json(row['data'])
            if (self.lineage(scope, record)[1].get(proof.task_id) != reference or record.source_root != proof.source_root.path
                    or record.candidate is None):
                continue
            service._guard(scope, record, deep=True, read_only=True)
            yield record

    def invalidate(self, scope, identity, reason, *, expected=None):
        """Revoke dependent ownership atomically; stopped proof remains audit data."""
        store = self.store
        if not store.db.in_transaction:
            raise RuntimeError('Completion invalidation must be atomic')
        record = self.get(scope, identity)
        task = store.task(scope, record.task_id)
        if (expected is not None and record != expected) or task.completion_id != record.id or self._invalidated(record):
            return ()
        workflow = store.workflow(scope, record.workflow_id)
        store._event(scope, workflow, 'task_completion_revoked', task=task, completion_id=record.id, reason=reason)
        return store._invalidate_dependencies(scope, task, reason, include_task=True, completion_id=record.id)

    def refresh(self, scope, identity):
        if self.store.db.in_transaction:
            raise RuntimeError('Completion evidence inspection cannot run in a transaction')
        record = self.get(scope, identity)
        if not self.current(scope, identity):
            try:
                if next(self._applying(scope, record), None) is not None:
                    # The owned writer may be between file installs. This is pending,
                    # never a pass; its final journal and verdict still gate completion.
                    return record
            except (OSError, ValueError):
                pass
            with self.store.transaction():
                self.invalidate(scope, identity, 'completion_not_current', expected=record)
        return record

    def complete(self, scope, artifact_id, review_id, *, expected_revision, key):
        store = self.store
        store._role(scope, ('owner', 'runtime'))
        if store.db.in_transaction:
            raise RuntimeError('Task completion preflight cannot run in a transaction')
        store._host_guard()
        request = {'complete_task': artifact_id, 'review': review_id, 'revision': expected_revision, 'root': str(self.root)}
        with store.transaction():
            store._host_sql()
            found, saved, _ = store._replay(scope, key, request)
            if found:
                return TaskCompletion.model_validate(saved)
            facts = self._facts(scope, artifact_id, review_id)
            artifact, review, task, author, reviewer, _, workflow = facts
            self._can_complete(task, author, workflow, expected_revision, review)
            prerequisites = self._inputs(scope, task, {}, frozenset())
            if prerequisites != author.prerequisites:
                raise StoreError('completion_dependency')
        artifacts, sources = Artifacts(store, self.root), Sources(store)
        runtime_root = sources.prepare(self.root)
        workspace = artifacts.workspaces.get(scope, author.id)
        source = sources.prepare(Path(workspace.source_root))
        registered_source(store, task.project_id, source.path)
        pins = self._evidence_pins(scope, review.id, prerequisites)
        if capture(Path(source.path)).subject_hash != artifact.base.subject_hash:
            raise StoreError('completion_evidence')
        self._review_evidence(scope, review.id)
        self._input_evidence(scope, prerequisites, {})
        if (capture(Path(source.path)).subject_hash != artifact.base.subject_hash
                or sources.prepare(self.root) != runtime_root or sources.prepare(Path(source.path)) != source
                or self._evidence_pins(scope, review.id, prerequisites) != pins):
            raise StoreError('completion_changed')
        with store.transaction():
            store._host_sql()
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return TaskCompletion.model_validate(saved)
            if self._facts(scope, artifact_id, review_id) != facts or self._inputs(scope, task, {}, frozenset()) != prerequisites:
                raise StoreError('revision')
            now = self._can_complete(task, author, workflow, expected_revision, review)
            record = TaskCompletion(id=uuid4().hex, project_id=task.project_id, workflow_id=workflow.id, task_id=task.id,
                semantic_revision=task.semantic_revision, contract_hash=task.contract_hash, attempt_id=author.id,
                fence=author.fence, artifact_id=artifact.id, artifact_hash=_hash(artifact), review_id=review.id,
                review_hash=_hash(review), reviewer_task_id=reviewer.id, reviewer_semantic_revision=reviewer.semantic_revision,
                reviewer_contract_hash=reviewer.contract_hash, prerequisites=prerequisites, runtime_root=runtime_root,
                source_root=source, source_hash=artifact.base.subject_hash, created_at=now)
            store.db.execute('INSERT INTO task_completions VALUES (?,?,?,?)', (record.id, task.id, author.id, record.model_dump_json()))
            _succeed(store, scope, author, task, workflow, evidence_id=review.verdict.evidence_id)
            store._save_task(changed(store.task(scope, task.id), completion_id=record.id))
            store._event(scope, workflow, 'task_completion_published', task=task, attempt=author, completion_id=record.id,
                artifact_id=artifact.id, review_id=review.id, evidence_id=review.verdict.evidence_id)
            store._remember(scope, key, task.project_id, request_hash, record)
            return record

    def _can_complete(self, task, author, workflow, revision, review):
        if task.revision != revision:
            raise StoreError('revision', current=task)
        if (task.state != 'awaiting_review' or author.state != 'awaiting_review'
                or task.current_attempt_id != author.id or task.fence != author.fence + 1 or task.completion_id is not None):
            raise StoreError('fence')
        if task.paused or workflow.state != 'running' or self.store._counter('queue_paused'):
            raise StoreError('paused')
        now = self.store.clock()
        if (workflow.deadline_at is None or now >= workflow.deadline_at
                or now < max(author.created_at, review.created_at)):
            raise StoreError('wall_deadline')
        return now
