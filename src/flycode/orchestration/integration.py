"""One host integrator: exact reviews, merged checks, journaled apply and completion."""

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from ..domain import ToolAction
from ..domain.results import IntegrationRecord
from ..domain.verification import SubjectRef
from ..verification.acceptance import input_identity
from ..verification.engine import VerificationEngine
from ..verification.plan import PlanBuilder
from ..verification.snapshot import capture
from .application import Application
from .artifacts import Artifacts
from .review import Reviews
from .store import ACTIVE, Store, StoreError, changed, covers, required_task
from .tools import Tools
from .verification import CheckExecutor, _succeed, accepted


class Integration:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.artifacts, self.reviews = Artifacts(store, root), Reviews(store, root)
        self.engine = self.reviews.engine

    def get(self, scope, identity) -> IntegrationRecord:
        record = self.store._load('integrations', identity, IntegrationRecord)
        self.store.workflow(scope, record.workflow_id)
        return record

    def _save(self, scope, record, **changes):
        record = changed(record, **changes)
        with self.store.transaction():
            self.store.workflow(scope, record.workflow_id)
            self.store.db.execute('UPDATE integrations SET state=?,data=? WHERE id=?',
                (record.state, record.model_dump_json(), record.id))
        return record

    def _reviewed(self, scope, record):
        for identity, expected in record.artifact_hashes.items():
            if self.artifacts.get(scope, identity).patch_hash != expected:
                raise StoreError('artifact_integrity')
        reviewed = set()
        for identity in record.review_ids:
            review = self.reviews.get(scope, identity)
            reviewer = self.store._attempt(review.reviewer_attempt_id)
            task = self.store.task(scope, reviewer.task_id)
            if (review.status != 'approved' or task.state != 'succeeded'
                    or reviewer.state != 'succeeded' or not reviewer.stop_confirmed
                    or reviewer.semantic_revision != task.semantic_revision
                    or review.reviewer_contract_hash != task.contract_hash
                    or record.artifact_hashes.get(review.artifact_id) != review.patch_hash
                    or not accepted(self.engine, review.verdict, self.artifacts.directory / review.artifact_id / 'target', task.acceptance)):
                raise StoreError('review_changed')
            reviewed.add(review.artifact_id)
        if reviewed != set(record.artifact_hashes):
            raise StoreError('review_changed')

    def _installed(self, scope, record):
        seen = set()
        while record.id not in seen:
            seen.add(record.id)
            directory = self.root / 'integrations' / record.id
            if (record.runtime_root != str(self.root)
                    or not Application(directory, Path(record.source_root), directory / 'baseline', directory / 'candidate').current(record.changes)):
                return False
            if record.previous_result_id is None:
                return True
            previous = self.get(scope, record.previous_result_id)
            if previous.workflow_id != record.workflow_id or previous.source_root != record.source_root:
                return False
            record = previous
        return False

    def current(self, scope, identity) -> bool:
        if self.store.db.in_transaction:
            raise RuntimeError('Result inspection cannot run in a SQLite transaction')
        record = self.get(scope, identity)
        if record.state != 'completed':
            return False
        try:
            workflow = self.store.workflow(scope, record.workflow_id)
            tasks = [task for task in self.store.tasks(scope, workflow.id) if required_task(task)]
            if (record.runtime_root != str(self.root) or workflow.state != 'succeeded'
                    or {task.id: task.contract_hash for task in tasks} != record.contracts
                    or {task.id: task.semantic_revision for task in tasks} != record.semantic_revisions
                    or any(task.state != 'succeeded' or task.current_attempt_id is not None
                           or task.result_id != record.id for task in tasks)):
                return False
            source = Path(record.source_root)
            if not self._installed(scope, record):
                return False
            self._reviewed(scope, record)
            expected = set(record.author_attempts) | {workflow.root_task_id, self.store._attempt(record.attempt_id).task_id}
            if set(record.applied_verdicts) != expected or set(record.approval_hashes) != expected:
                return False
            for task_id, verdict in record.applied_verdicts.items():
                if not accepted(self.engine, verdict, source, self.store.task(scope, task_id).acceptance):
                    return False
            return not any(attempt.state in (*ACTIVE, 'awaiting_review') for attempt in self.store.attempts(scope, workflow.id))
        except (OSError, ValueError):
            return False

    def refresh(self, scope, identity):
        record = self.get(scope, identity)
        if record.state != 'completed' or self.current(scope, identity):
            return record
        with self.store.transaction():
            return self.store._invalidate_result(scope, identity, 'result_changed', expected=record)

    def _guard(self, scope, record, *, deep=False):
        attempt, task, workflow = self.store._owned(scope, record.attempt_id, record.fence, running=True)
        if task.kind != 'review' or attempt.profile_snapshot.role != 'reviewer':
            raise StoreError('permission')
        if task.paused or workflow.state != 'running' or self.store._counter('queue_paused'):
            raise StoreError('paused')
        if self.store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt.id,)).fetchone():
            raise StoreError('worker_exists')
        if any(item.state in ACTIVE and item.id != attempt.id for item in self.store.attempts(scope, workflow.id)):
            raise StoreError('incomplete_workflow', 'Every other executor must be stopped before integration')
        root = self.store.task(scope, workflow.root_task_id)
        if root.id not in record.author_attempts and root.current_attempt_id is not None:
            raise StoreError('incomplete_workflow', 'An unselected root assignment cannot be completed as an aggregate')
        tasks = [item for item in self.store.tasks(scope, workflow.id) if required_task(item)]
        if ({item.id: item.contract_hash for item in tasks} != record.contracts
                or {item.id: item.semantic_revision for item in tasks} != record.semantic_revisions):
            raise StoreError('contract_changed')
        if any(item.paused or item.permissions is None or not item.permissions.approved for item in tasks):
            raise StoreError('completion_blocked', 'An owner paused work or revoked its permission')
        for task_id, identity in record.author_attempts.items():
            original = self.store.task(scope, task_id)
            author = self.store._attempt(identity)
            if record.previous_result_id is not None:
                if original.current_attempt_id is not None or author.state != 'succeeded' or not author.stop_confirmed:
                    raise StoreError('fence')
                if original.state not in {'planned', 'ready', 'blocked', 'succeeded'}:
                    raise StoreError('completion_blocked', 'Reopen stopped work before revalidation')
            elif (original.current_attempt_id != identity or original.state != 'awaiting_review'
                    or original.fence != author.fence + 1 or author.state != 'awaiting_review'
                    or not author.stop_confirmed):
                raise StoreError('fence')
        if deep:
            self._reviewed(scope, record)
        return attempt, task, workflow

    def _verify(self, scope, record, executor, approvals, *, applied):
        directory = self.root / 'integrations' / record.id
        artifacts = [self.artifacts.get(scope, identity) for identity in record.artifact_hashes]
        by_task = {artifact.task_id: artifact for artifact in artifacts}
        workflow = self.store.workflow(scope, record.workflow_id)
        tasks = set(by_task) | {workflow.root_task_id, self.store._attempt(record.attempt_id).task_id}
        results = {}
        if set(approvals) != tasks:
            raise StoreError('approval_scope')
        for task_id in sorted(tasks):
            self._guard(scope, record, deep=True)
            task = self.store.task(scope, task_id)
            artifact = by_task.get(task_id, artifacts[0])
            merged = Path(record.source_root) if applied else directory / 'candidate'
            roots = {'baseline': self.artifacts.directory / artifact.id / 'baseline',
                'target': self.artifacts.directory / artifact.id / 'target' if task_id in by_task else directory / 'candidate',
                'regression': merged, 'integration': merged}
            ref = SubjectRef(project_id=task.project_id, workflow_id=workflow.id, task_id=task.id,
                attempt_id=record.author_attempts.get(task.id, record.attempt_id))
            snapshots = {stage: capture(root, ref=ref) for stage, root in roots.items()}
            approved = self.engine.oracles.read(approvals[task.id])
            plan = PlanBuilder().build(snapshots['integration'], task.acceptance, approved.checks,
                subjects=snapshots, approval_hash=approvals[task.id], reruns=record.reruns)
            engine = VerificationEngine(self.engine.data_dir, invocation_executor=executor.execute)
            verdict = engine.verify_plan(plan, roots, should_stop=executor.should_stop)
            results[task.id] = verdict
            record = self._save(scope, record, **{'applied_verdicts' if applied else 'verdicts': results})
            if not accepted(engine, verdict, merged, task.acceptance):
                raise StoreError('verification_failed', current=verdict)
        return record

    def run(self, scope, attempt_id, fence, artifact_ids, review_ids, *, approval_hashes, reruns=0,
            previous_result_id=None, trust='trusted-local-process'):
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Integration cannot run in a SQLite transaction')
        if trust != 'trusted-local-process':
            raise StoreError('unsupported_sandbox')
        previous = store.db.execute('SELECT id FROM integrations WHERE attempt_id=?', (attempt_id,)).fetchone()
        if previous:
            record = self.get(scope, previous['id'])
            if (record.fence != fence or tuple(record.artifact_hashes) != tuple(artifact_ids)
                    or record.review_ids != tuple(review_ids) or record.approval_hashes != approval_hashes
                    or record.reruns != reruns or record.previous_result_id != previous_result_id):
                raise StoreError('idempotency')
            record = self.refresh(scope, record.id)
            if record.state != 'completed':
                raise StoreError('reconcile_required' if record.state != 'invalidated' else 'result_invalidated', current=record)
            return record
        store._execution_available(scope, attempt_id)
        attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        if any(item.state in ACTIVE and item.id != attempt.id for item in store.attempts(scope, workflow.id)):
            raise StoreError('incomplete_workflow', 'Every other executor must be stopped before integration')
        artifacts = [self.artifacts.get(scope, identity) for identity in artifact_ids]
        if not artifacts or len(set(artifact_ids)) != len(artifact_ids) or len(set(review_ids)) != len(review_ids):
            raise StoreError('integration_scope')
        hashes = {artifact.id: artifact.patch_hash for artifact in artifacts}
        if task.input_artifacts != hashes or any(artifact.workflow_id != workflow.id for artifact in artifacts):
            raise StoreError('integration_scope')
        reviews = [self.reviews.get(scope, identity) for identity in review_ids]
        if {review.artifact_id for review in reviews} != set(hashes) or len(reviews) != len(hashes):
            raise StoreError('review_scope')
        sources = {self.artifacts.workspaces.get(scope, artifact.attempt_id).source_root for artifact in artifacts}
        if len(sources) != 1:
            raise StoreError('integration_scope')
        source = Path(sources.pop())
        before = capture(source)
        previous_result = self.get(scope, previous_result_id) if previous_result_id is not None else None
        if previous_result is not None:
            if (previous_result.state != 'invalidated' or previous_result.workflow_id != workflow.id
                    or previous_result.source_root != str(source) or previous_result.artifact_hashes != hashes
                    or previous_result.candidate is None
                    or before.files != previous_result.candidate.files or before.modes != previous_result.candidate.modes
                    or before.base != previous_result.source_before.base or not self._installed(scope, previous_result)):
                raise StoreError('integration_conflict', 'The previously applied source is no longer compatible')
            if set(review_ids) & set(previous_result.review_ids):
                raise StoreError('review_changed', 'A reopened result needs fresh independent reviews')
        elif any(artifact.base.subject_hash != before.subject_hash for artifact in artifacts):
            raise StoreError('integration_conflict', 'The captured source base changed')
        author_attempts = {artifact.task_id: artifact.attempt_id for artifact in artifacts}
        tasks = [item for item in store.tasks(scope, workflow.id) if required_task(item)]
        root_task = store.task(scope, workflow.root_task_id)
        if root_task.id not in author_attempts and root_task.current_attempt_id is not None:
            raise StoreError('incomplete_workflow', 'An unselected root assignment cannot be completed as an aggregate')
        if any(item.state != 'succeeded' and item.id not in {*author_attempts, task.id, workflow.root_task_id} for item in tasks):
            raise StoreError('incomplete_workflow')
        changes = {}
        for artifact in artifacts:
            for change in artifact.changes:
                if change.path in changes and change != changes[change.path]:
                    raise StoreError('integration_conflict', 'Overlapping reviewed patches require resolution')
                if root_task.permissions is None or not root_task.permissions.approved or not covers(root_task.permissions.write_scopes, (change.path,)):
                    raise StoreError('permission')
                changes[change.path] = change
        record = IntegrationRecord(id=uuid4().hex, project_id=task.project_id, workflow_id=workflow.id,
            attempt_id=attempt_id, fence=fence, source_root=str(source), runtime_root=str(self.root), source_before=before,
            previous_result_id=previous_result_id,
            artifact_hashes=hashes, review_ids=tuple(review_ids), contracts={item.id: item.contract_hash for item in tasks},
            semantic_revisions={item.id: item.semantic_revision for item in tasks}, approval_hashes=approval_hashes, reruns=reruns,
            author_attempts=author_attempts, changes=() if previous_result is not None else tuple(changes[path] for path in sorted(changes)),
            created_at=store.clock())
        self._guard(scope, record, deep=True)
        with store.transaction():
            self._guard(scope, record)
            if store.db.execute("SELECT 1 FROM integrations WHERE workflow_id=? AND state IN ('preparing','verifying','applying','reconciling')",
                                (workflow.id,)).fetchone():
                raise StoreError('integration_busy')
            store._claim_execution(scope, attempt_id, fence, 'integration')
            store.db.execute('INSERT INTO integrations VALUES (?,?,?,?,?)', (record.id, workflow.id, attempt_id, record.state, record.model_dump_json()))
            store._event(scope, workflow, 'integration_started', task=task, attempt=attempt, integration_id=record.id)
        directory = self.root / 'integrations' / record.id
        directory.mkdir(parents=True, mode=0o700)
        application = Application(directory, source, directory / 'baseline', directory / 'candidate')
        executor = CheckExecutor(store, scope, attempt_id, fence)
        apply_call = None
        try:
            if capture(source, directory / 'baseline').subject_hash != before.subject_hash:
                raise StoreError('integration_conflict')
            shutil.copytree(directory / 'baseline', directory / 'candidate')
            for artifact in artifacts if previous_result is None else ():
                for change in artifact.changes:
                    target = directory / 'candidate' / change.path
                    if change.after is None:
                        target.unlink()
                    else:
                        data = (self.artifacts.directory / artifact.id / 'target' / change.path).read_bytes()
                        if hashlib.sha256(data).hexdigest() != change.after.sha256:
                            raise StoreError('artifact_integrity')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        target.chmod(change.after.mode)
            candidate = capture(directory / 'candidate')
            record = self._save(scope, record, state='verifying', candidate=candidate)
            record = self._verify(scope, record, executor, approval_hashes, applied=False)
            self._guard(scope, record, deep=True)
            if (capture(source).subject_hash != before.subject_hash
                    or input_identity(directory / 'candidate') != (candidate.files, candidate.modes)):
                raise StoreError('integration_conflict')
            record = self._save(scope, record, state='applying')
            # Trusted integrator operation, authorized by the root's write scopes and exact reviews.
            # It is not exposed as a reviewer/model tool or permission grant.
            if previous_result is None:
                apply_call, _ = Tools(store)._begin(scope, attempt_id, fence, ToolAction(id=uuid4().hex,
                    tool='integrate', source='host', arguments={'patches': hashes, 'source_hash': before.subject_hash}))
            application.apply(record.changes, lambda: self._guard(scope, record))
            after = capture(source)
            if after.files != candidate.files or after.modes != candidate.modes or after.base != before.base:
                raise StoreError('integration_conflict')
            if apply_call is not None:
                Tools(store).finish(scope, apply_call.id, status='completed')
            apply_call = None
            record = self._verify(scope, record, executor, approval_hashes, applied=True)
            self._guard(scope, record, deep=True)
            if not self._installed(scope, record):
                raise StoreError('integration_conflict', 'Applied or retained source objects changed')
            for task_id, verdict in record.applied_verdicts.items():
                if not accepted(self.engine, verdict, source, store.task(scope, task_id).acceptance):
                    raise StoreError('verification_changed')
            with store.transaction():
                attempt, task, workflow = self._guard(scope, record)
                for task_id, author_id in record.author_attempts.items():
                    original = store.task(scope, task_id)
                    if previous_result is None:
                        _succeed(store, scope, store._attempt(author_id), original, workflow,
                            evidence_id=record.applied_verdicts[task_id].evidence_id)
                    else:
                        store._save_task(changed(original, state='succeeded', revision=original.revision + 1, updated_at=store.clock()))
                        store._event(scope, workflow, 'verified_task_revalidated', task=original,
                            integration_id=record.id, evidence_id=record.applied_verdicts[task_id].evidence_id)
                _succeed(store, scope, attempt, task, workflow, evidence_id=record.applied_verdicts[task.id].evidence_id)
                root_task = store.task(scope, workflow.root_task_id)
                if root_task.state != 'succeeded' and root_task.id not in author_attempts:
                    store._save_task(changed(root_task, state='succeeded', revision=root_task.revision + 1, updated_at=store.clock()))
                required = [item for item in store.tasks(scope, workflow.id) if required_task(item)]
                if (any(item.state != 'succeeded' or item.current_attempt_id is not None for item in required)
                        or any(item.state in (*ACTIVE, 'awaiting_review') for item in store.attempts(scope, workflow.id))):
                    raise StoreError('incomplete_workflow')
                for item in required:
                    store._save_task(changed(item, result_id=record.id, blocked_reason=None, revision=item.revision + 1))
                store._save_workflow(changed(workflow, state='succeeded', active_slot=False, revision=workflow.revision + 1))
                record = changed(record, state='completed')
                store.db.execute('UPDATE integrations SET state=?,data=? WHERE id=?', (record.state, record.model_dump_json(), record.id))
                store._event(scope, workflow, 'integration_completed', task=task, attempt=attempt, integration_id=record.id)
            return record
        except (OSError, ValueError) as error:
            record = self.get(scope, record.id)
            clean = application.rollback(record.changes)
            if apply_call is not None:
                Tools(store).finish(scope, apply_call.id, status='failed' if clean else 'uncertain', error_class=type(error).__name__)
            record = self._save(scope, record, state='failed' if clean else 'reconciling', error=getattr(error, 'code', type(error).__name__))
            if clean:
                try:
                    store.confirm_stopped(scope, attempt_id, fence, outcome='failed', expenditure_confirmed=True)
                except StoreError as stop_error:
                    if stop_error.code not in {'resource_busy', 'uncertain'}:
                        raise
            raise
