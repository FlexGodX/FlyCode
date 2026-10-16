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
from .store import ACTIVE, Store, StoreError, changed, covers
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
        tasks = self.store.tasks(scope, workflow.id)
        if {item.id: item.contract_hash for item in tasks} != record.contracts:
            raise StoreError('contract_changed')
        for task_id, identity in record.author_attempts.items():
            original = self.store.task(scope, task_id)
            author = self.store._attempt(identity)
            if (original.current_attempt_id != identity or original.state != 'awaiting_review'
                    or original.fence != author.fence + 1 or author.state != 'awaiting_review'
                    or not author.stop_confirmed):
                raise StoreError('fence')
        if deep:
            for identity, expected in record.artifact_hashes.items():
                artifact = self.artifacts.get(scope, identity)
                if artifact.patch_hash != expected:
                    raise StoreError('artifact_integrity')
            for identity in record.review_ids:
                review = self.reviews.get(scope, identity)
                reviewer = self.store._attempt(review.reviewer_attempt_id)
                reviewed_task = self.store.task(scope, reviewer.task_id)
                if (review.status != 'approved' or reviewed_task.state != 'succeeded'
                        or review.reviewer_contract_hash != reviewed_task.contract_hash
                        or record.artifact_hashes.get(review.artifact_id) != review.patch_hash
                        or not accepted(self.engine, review.verdict, self.artifacts.directory / review.artifact_id / 'target', reviewed_task.acceptance)):
                    raise StoreError('review_changed')
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
                subjects=snapshots, approval_hash=approvals[task.id])
            engine = VerificationEngine(self.engine.data_dir, invocation_executor=executor.execute)
            verdict = engine.verify_plan(plan, roots, should_stop=executor.should_stop)
            results[task.id] = verdict
            record = self._save(scope, record, **{'applied_verdicts' if applied else 'verdicts': results})
            if not accepted(engine, verdict, merged, task.acceptance):
                raise StoreError('verification_failed', current=verdict)
        return record

    def run(self, scope, attempt_id, fence, artifact_ids, review_ids, *, approval_hashes, trust='trusted-local-process'):
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Integration cannot run in a SQLite transaction')
        if trust != 'trusted-local-process':
            raise StoreError('unsupported_sandbox')
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
        if any(artifact.base.subject_hash != before.subject_hash for artifact in artifacts):
            raise StoreError('integration_conflict', 'The captured source base changed')
        author_attempts = {artifact.task_id: artifact.attempt_id for artifact in artifacts}
        tasks = store.tasks(scope, workflow.id)
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
            attempt_id=attempt_id, fence=fence, source_root=str(source), source_before=before,
            artifact_hashes=hashes, review_ids=tuple(review_ids), contracts={item.id: item.contract_hash for item in tasks},
            author_attempts=author_attempts, changes=tuple(changes[path] for path in sorted(changes)), created_at=store.clock())
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
            for artifact in artifacts:
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
            apply_call, _ = Tools(store)._begin(scope, attempt_id, fence, ToolAction(id=uuid4().hex,
                tool='integrate', source='host', arguments={'patches': hashes, 'source_hash': before.subject_hash}))
            application.apply(record.changes, lambda: self._guard(scope, record))
            after = capture(source)
            if after.files != candidate.files or after.modes != candidate.modes or after.base != before.base:
                raise StoreError('integration_conflict')
            Tools(store).finish(scope, apply_call.id, status='completed')
            apply_call = None
            record = self._verify(scope, record, executor, approval_hashes, applied=True)
            self._guard(scope, record, deep=True)
            if not application.current(record.changes):
                raise StoreError('integration_conflict', 'Applied or retained source objects changed')
            for task_id, verdict in record.applied_verdicts.items():
                if not accepted(self.engine, verdict, source, store.task(scope, task_id).acceptance):
                    raise StoreError('verification_changed')
            with store.transaction():
                attempt, task, workflow = self._guard(scope, record)
                for task_id, author_id in record.author_attempts.items():
                    _succeed(store, scope, store._attempt(author_id), store.task(scope, task_id), workflow,
                        evidence_id=record.applied_verdicts[task_id].evidence_id)
                _succeed(store, scope, attempt, task, workflow, evidence_id=record.applied_verdicts[task.id].evidence_id)
                root_task = store.task(scope, workflow.root_task_id)
                if root_task.state != 'succeeded' and root_task.id not in author_attempts:
                    store._save_task(changed(root_task, state='succeeded', revision=root_task.revision + 1, updated_at=store.clock()))
                if (any(item.state != 'succeeded' or item.current_attempt_id is not None for item in store.tasks(scope, workflow.id))
                        or any(item.state in (*ACTIVE, 'awaiting_review') for item in store.attempts(scope, workflow.id))):
                    raise StoreError('incomplete_workflow')
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
