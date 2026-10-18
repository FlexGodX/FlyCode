"""One host integrator: exact reviews, merged checks, journaled apply and completion."""

import hashlib
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from ..domain import ToolAction
from ..domain.orchestration import TaskDraft
from ..domain.results import IntegrationRecord
from ..domain.verification import SubjectRef
from ..verification.acceptance import input_identity
from ..verification.custody import locked, stopped
from ..verification.engine import VerificationEngine
from ..verification.evidence import read_regular
from ..verification.plan import PlanBuilder
from ..verification.snapshot import capture
from .application import Application
from .artifacts import Artifacts
from .inputs import Inputs
from .projects import Projects
from .review import Reviews
from .sources import Sources
from .store import (
    ACTIVE,
    Store,
    StoreError,
    changed,
    covers,
    required_task,
    structural_aggregate,
)
from .tools import Tools
from .verification import CheckExecutor, _succeed, accepted


def registered_source(store, project_id, source):
    """Historical IDs retain compatibility; registered IDs require physical authority."""
    if not store.db.execute('SELECT 1 FROM canonical_projects WHERE id=?', (project_id,)).fetchone():
        return
    if store.db.in_transaction:
        raise RuntimeError('Project inspection cannot run in a SQLite transaction')
    if Projects(store).resolve(Path(source)).id != project_id:
        raise StoreError('project_scope')


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

    def _requirements(self, scope, record):
        workflow = self.store.workflow(scope, record.workflow_id)
        tasks = set(record.author_attempts) | {workflow.root_task_id, self.store._attempt(record.attempt_id).task_id}
        tasks.update(self.aggregate_tasks(scope, workflow.id, tasks))
        for identity in record.resolved_conflicts:
            tasks.update(self._requirements(scope, self.get(scope, identity)))
        return tasks

    def aggregate_tasks(self, scope, workflow_id, excluded):
        """Structural parents have no executable profile, budget or prior attempt."""
        tasks = [item for item in self.store.tasks(scope, workflow_id) if required_task(item)]
        attempts = self.store.attempts(scope, workflow_id)
        return {item.id for item in tasks if item.id not in excluded and structural_aggregate(item, tasks, attempts)}

    def _lineage_basis(self, scope, record):
        """Prove each explicit root reopen without rewriting the original conflict."""
        root = self.store.workflow(scope, record.workflow_id).root_task_id
        seen = {record.id}
        while record.previous_result_id is not None:
            previous = self.get(scope, record.previous_result_id)
            if (previous.id in seen or previous.state != 'invalidated'
                    or previous.workflow_id != record.workflow_id or previous.runtime_root != record.runtime_root
                    or previous.source_root != record.source_root or previous.artifact_hashes != record.artifact_hashes
                    or previous.resolved_conflicts != record.resolved_conflicts or record.reruns < previous.reruns
                    or record.semantic_revisions.get(root) != previous.semantic_revisions.get(root, -2) + 1):
                raise StoreError('resolution_scope')
            for identity in previous.contracts.keys() & record.contracts.keys():
                if (record.contracts[identity] != previous.contracts[identity]
                        or (identity != root and record.semantic_revisions.get(identity) != previous.semantic_revisions.get(identity))):
                    raise StoreError('contract_changed')
            for identity in previous.approval_hashes.keys() & record.approval_hashes.keys():
                if record.approval_hashes[identity] != previous.approval_hashes[identity]:
                    raise StoreError('approval_changed')
            seen.add(previous.id)
            record = previous
        return record

    def _conflict_reviews(self, scope, record):
        reviews = {}
        for identity in record.resolved_conflicts:
            conflict = self.get(scope, identity)
            for review_id in conflict.review_ids:
                review = self.reviews.get(scope, review_id)
                reviews[self.store._attempt(review.reviewer_attempt_id).task_id] = review
            reviews.update(self._conflict_reviews(scope, conflict))
        return reviews

    def _restorable_reviews(self, scope, record):
        if record.previous_result_id is None:
            return {}
        previous = self.get(scope, record.previous_result_id)
        restored = {}
        for identity, review in self._conflict_reviews(scope, record).items():
            task = self.store.task(scope, identity)
            attempt = self.store._attempt(review.reviewer_attempt_id)
            if (required_task(task) and task.kind == 'review' and task.state == 'blocked'
                    and task.blocked_reason == 'result_invalidated' and task.result_id == previous.id
                    and task.current_attempt_id is None and task.fence == attempt.fence + 1
                    and not task.paused and task.permissions is not None and task.permissions.approved
                    and attempt.state == 'succeeded' and attempt.stop_confirmed
                    and task.semantic_revision == attempt.semantic_revision == previous.semantic_revisions.get(identity)
                    and task.contract_hash == review.reviewer_contract_hash == previous.contracts.get(identity)
                    and record.contracts.get(identity) == task.contract_hash
                    and record.semantic_revisions.get(identity) == task.semantic_revision):
                restored[identity] = review
        return restored

    def _resolution_guard(self, scope, conflict, *, deep=False, context=None):
        from .completions import Completions

        store = self.store
        if conflict.state != 'conflict' or conflict.runtime_root != str(self.root):
            raise StoreError('resolution_scope')
        for identity, expected in conflict.contracts.items():
            task = store.task(scope, identity)
            semantic = conflict.semantic_revisions.get(identity)
            if context is not None and identity == store.workflow(scope, conflict.workflow_id).root_task_id:
                basis = self._lineage_basis(scope, context)
                if basis.contracts.get(identity) != expected or basis.semantic_revisions.get(identity) != semantic:
                    raise StoreError('contract_changed')
                semantic = context.semantic_revisions.get(identity)
            if (not required_task(task) or task.contract_hash != expected
                    or task.semantic_revision != semantic):
                raise StoreError('contract_changed')
            if task.paused or task.permissions is None or not task.permissions.approved:
                raise StoreError('completion_blocked')
        for task_id, identity in conflict.author_attempts.items():
            task, author = store.task(scope, task_id), store._attempt(identity)
            awaiting = (not conflict.resolution_attempts and author.state == 'awaiting_review'
                and task.current_attempt_id == author.id and task.fence == author.fence + 1)
            released = (author.state == 'interrupted' and task.current_attempt_id is None and task.fence == author.fence + 2)
            completed = (task_id in conflict.author_completions and author.state == task.state == 'succeeded'
                and task.current_attempt_id is None and task.fence == author.fence + 2)
            reopened_root = (context is not None and context.previous_result_id is not None
                and task_id == store.workflow(scope, conflict.workflow_id).root_task_id
                and task.state in {'planned', 'ready'} and task.result_id is None)
            retained = (context is not None and context.previous_result_id is not None
                and task_id in conflict.author_completions and author.state == 'succeeded'
                and ((task.state == 'blocked' and task.blocked_reason == 'result_invalidated'
                    and task.result_id == context.previous_result_id) or reopened_root)
                and task.current_attempt_id is None
                and task.fence == author.fence + 2)
            if not author.stop_confirmed or not (awaiting or released or completed or retained):
                raise StoreError('fence')
        Completions(store, self.root).require_bound(scope, conflict, coverage=False, context=context)
        integrator = store._attempt(conflict.attempt_id)
        task = store.task(scope, integrator.task_id)
        if (integrator.state != 'failed' or not integrator.stop_confirmed or task.current_attempt_id is not None
                or task.fence != integrator.fence + 1):
            raise StoreError('fence')
        if deep:
            self._reviewed(scope, conflict, context=context)
            for identity, approval in conflict.approval_hashes.items():
                self.engine.oracles.resolve(approval, store.task(scope, identity).acceptance)

    def _lineage_guard(self, scope, record, *, deep=False, context=None, coverage=True):
        from .completions import Completions

        Completions(self.store, self.root).require_bound(scope, record, deep=deep, coverage=coverage, context=context)
        context = record if context is None else context
        artifacts = [self.artifacts._record(scope, identity) for identity in record.artifact_hashes]
        for identity in record.resolved_conflicts:
            conflict = self.get(scope, identity)
            selected = [item for item in artifacts if item.task_id == conflict.resolution_task_id]
            if (conflict.workflow_id != record.workflow_id or conflict.source_root != record.source_root
                    or len(selected) != 1 or selected[0].attempt_id not in conflict.resolution_attempts
                    or selected[0].base.subject_hash != conflict.source_before.subject_hash):
                raise StoreError('resolution_scope')
            self._resolution_guard(scope, conflict, deep=deep, context=context)
            self._lineage_guard(scope, conflict, deep=deep, context=context, coverage=False)

    def begin_resolution(self, scope, attempt_id, fence, workspace):
        store = self.store
        attempt, task, workflow = store._owned(scope, attempt_id, fence)
        conflict = next((self.get(scope, row['id']) for row in store.db.execute(
            "SELECT id FROM integrations WHERE workflow_id=? AND state='conflict'", (workflow.id,))
            if self.get(scope, row['id']).resolution_task_id == task.id), None)
        if conflict is None:
            return None
        if (task.kind != 'implementation' or attempt.profile_snapshot.role != 'developer'
                or task.input_artifacts != conflict.artifact_hashes
                or task.base_snapshot_hash != conflict.source_before.subject_hash
                or workspace.source_root != conflict.source_root
                or workspace.snapshot.subject_hash != conflict.source_before.subject_hash
                or task.permissions is None or not task.permissions.approved or 'read_file' not in task.permissions.tools
                or not covers(task.permissions.write_scopes, conflict.conflict_paths)):
            raise StoreError('resolution_scope')
        self._resolution_guard(scope, conflict, deep=True)
        self._lineage_guard(scope, conflict, deep=True, coverage=False)
        with store.transaction():
            attempt, task, workflow = store._owned(scope, attempt_id, fence)
            if task.paused or workflow.state != 'running' or store._counter('queue_paused'):
                raise StoreError('paused')
            conflict = self.get(scope, conflict.id)
            self._resolution_guard(scope, conflict)
            self._lineage_guard(scope, conflict, coverage=False)
            if attempt.id in conflict.resolution_attempts:
                return conflict
            for identity in conflict.author_attempts:
                original = store.task(scope, identity)
                if identity in conflict.author_completions:
                    continue
                original = store._stop(original, outcome='blocked')
                store._save_task(changed(original, state='blocked', blocked_reason='resolution_pending',
                    revision=original.revision + 1, updated_at=store.clock()))
            conflict = changed(conflict, resolution_attempts=(*conflict.resolution_attempts, attempt.id))
            store.db.execute('UPDATE integrations SET data=? WHERE id=?', (conflict.model_dump_json(), conflict.id))
            store._event(scope, workflow, 'resolution_started', task=task, attempt=attempt, integration_id=conflict.id)
            return conflict

    def _reviewed(self, scope, record, *, context=None):
        for identity, expected in record.artifact_hashes.items():
            artifact = self.artifacts.get(scope, identity)
            if artifact.patch_hash != expected:
                raise StoreError('artifact_integrity')
            Inputs(self.store).require_artifact(scope, artifact)
        reviewed = set()
        restoring = self._restorable_reviews(scope, context) if context is not None else {}
        for identity in record.review_ids:
            review = self.reviews.get(scope, identity)
            reviewer = self.store._attempt(review.reviewer_attempt_id)
            task = self.store.task(scope, reviewer.task_id)
            if (review.status != 'approved' or (task.state != 'succeeded' and restoring.get(task.id) != review)
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
            self._lineage_guard(scope, record, deep=True)
            expected = self._requirements(scope, record)
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

    def _settle_source(self, scope, identity):
        row = self.store.db.execute('SELECT id FROM integrations WHERE id=?', (identity,)).fetchone()
        if row is not None:
            record = self.get(scope, identity)
            if record.source_claim_id is not None:
                try:
                    Sources(self.store).settle(record.source_claim_id)
                except StoreError:
                    pass  # Durable claim stays held until trusted reconciliation proves settlement.

    def reconcile(self, scope, identity):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        if store.db.in_transaction:
            raise RuntimeError('Integration reconciliation cannot run in a SQLite transaction')
        record = self.get(scope, identity)
        attempt = store._attempt(record.attempt_id)
        if record.state == 'conflict':
            self._settle_source(scope, identity)
            return record
        if record.state in {'completed', 'invalidated'}:
            self._settle_source(scope, identity)
            return self.refresh(scope, identity)
        if record.state == 'failed' and attempt.stop_confirmed:
            self._settle_source(scope, identity)
            return record
        if record.runtime_root != str(self.root) or record.coordinator_identity is None:
            raise StoreError('ownership_unknown')
        try:
            with locked(self.root / 'integrations' / record.id / 'coordinator.lock', identity=record.coordinator_identity):
                return self._recover(scope, self.get(scope, identity))
        except BlockingIOError as error:
            raise StoreError('coordinator_running') from error
        except (OSError, ValueError) as error:
            if isinstance(error, StoreError):
                raise
            raise StoreError('ownership_unknown') from error
        finally:
            self._settle_source(scope, identity)

    def _recover(self, scope, record):
        store = self.store
        with store.transaction():
            record = self.get(scope, record.id)
            attempt = store._attempt(record.attempt_id)
            if record.state == 'failed' and attempt.stop_confirmed:
                return record
            task, workflow = store.task(scope, attempt.task_id), store.workflow(scope, record.workflow_id)
            if task.current_attempt_id != attempt.id or attempt.fence != record.fence:
                raise StoreError('fence')
            if attempt.state in {'assigned', 'running', 'stopping'}:
                attempt = store._reconcile(scope, workflow, attempt, 'integration_recovery')
            record = changed(record, state='reconciling', error='host_interrupted')
            store.db.execute('UPDATE integrations SET state=?,data=? WHERE id=?',
                (record.state, record.model_dump_json(), record.id))
        tools = Tools(store)
        calls = [call for call in tools.calls(scope, workflow.id) if call.attempt_id == attempt.id]
        with ExitStack() as locks:
            for call in calls:
                if call.process is not None:
                    try:
                        receipt = locks.enter_context(stopped(call.process))
                    except (OSError, ValueError):
                        return self._save(scope, record, error='process_unconfirmed')
                    if (not receipt.started and call.execution_profile == 'registered-pytest-v1'
                            and call.status in {'running', 'uncertain'}):
                        tools.finish(scope, call.id, status='failed', error_class='NeverStarted')
                elif call.execution_profile == 'registered-pytest-v1' and call.status in {'running', 'uncertain'}:
                    # The canonical binding commits before GO, so an unbound
                    # invocation of this versioned profile never executed pytest.
                    tools.finish(scope, call.id, status='failed', error_class='NeverAdmitted')
            calls = [call for call in tools.calls(scope, workflow.id) if call.attempt_id == attempt.id]
            unknown = any(call.status in {'running', 'uncertain'} and call.tool != 'integrate' for call in calls)
            unknown |= store.db.execute("SELECT 1 FROM model_calls WHERE attempt_id=? AND state IN ('running','uncertain')",
                (attempt.id,)).fetchone() is not None
            if unknown:
                return self._save(scope, record, error='effects_unknown')
            directory = self.root / 'integrations' / record.id
            rollback_error = 'rollback_conflict'
            try:
                self._source_guard(record)
                clean = Application(directory, Path(record.source_root), directory / 'baseline', directory / 'candidate').rollback(
                    record.changes, lambda: self._source_guard(record))
            except (OSError, ValueError) as error:
                clean = False
                rollback_error = getattr(error, 'code', 'rollback_conflict')
            if not clean:
                return self._save(scope, record, error=rollback_error)
            for call in calls:
                if call.tool == 'integrate' and call.status in {'running', 'uncertain'}:
                    tools.finish(scope, call.id, status='failed', error_class='IntegrationAborted')
            with store.transaction():
                workflow = store.workflow(scope, workflow.id)
                state = workflow.state if workflow.state in {'cancelling', 'cancelled', 'failed', 'pausing', 'paused'} else 'incomplete'
                store._save_workflow(changed(workflow, state=state, revision=workflow.revision + 1))
                record = changed(record, state='failed', error='host_interrupted')
                store.db.execute('UPDATE integrations SET state=?,data=? WHERE id=?', (record.state, record.model_dump_json(), record.id))
            # A crash between these transactions retains the assignment. The
            # durable rolled-back journal makes the next reconciliation safe.
            store.confirm_stopped(scope, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
            with store.transaction():
                store._event(scope, workflow, 'integration_reconciled', task=task, attempt=attempt, integration_id=record.id)
            return record

    def _source_guard(self, record):
        # Recovery may revoke the attempt's task fence. Source custody remains
        # separate, and an unknown competing writer still vetoes rollback.
        if record.source_claim_id is None:
            raise StoreError('ownership_unknown')
        sources = Sources(self.store)
        claim = sources.guard(record.source_claim_id, record.id)
        if (claim.owner_kind != 'integration' or claim.attempt_id != record.attempt_id
                or claim.fence != record.fence or claim.root.path != record.source_root
                or claim.coordinator_identity != record.coordinator_identity
                or claim.coordinator_path != str(self.root / 'integrations' / record.id / 'coordinator.lock')):
            raise StoreError('source_owner')
        if not self.store.db.in_transaction:
            registered_source(self.store, self.store._workflow(record.workflow_id).project_id, record.source_root)
            if sources.prepare(Path(record.source_root)) != claim.root:
                raise StoreError('source_changed')

    def _guard(self, scope, record, *, deep=False, read_only=False):
        if read_only:
            from .host import HostRegistration, HostSession

            row = self.store.db.execute('SELECT data FROM host_registry WHERE id=1').fetchone()
            session = self.store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()
            if row is not None:
                registration = HostRegistration.model_validate_json(row['data'])
                current = HostSession.model_validate_json(session['data']) if session else None
                if (registration.database_path != str(self.store.path.absolute()) or current is None
                        or current.registration_id != registration.id or current.phase != 'ready'):
                    raise StoreError('host_fence')
            elif self.store._host_expected:
                raise StoreError('host_ownership_unknown')
        elif self.store.db.in_transaction:
            self.store._host_sql()
        else:
            self.store._host_guard()
        assignment = self.store._assignment if read_only else self.store._owned
        attempt, task, workflow = assignment(scope, record.attempt_id, record.fence, running=True)
        if record.source_claim_id is not None:
            self._source_guard(record)
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
            if task_id in record.author_completions and record.previous_result_id is None:
                if original.current_attempt_id is not None or original.state != 'succeeded' or author.state != 'succeeded' or not author.stop_confirmed:
                    raise StoreError('fence')
            elif record.previous_result_id is not None:
                if original.current_attempt_id is not None or author.state != 'succeeded' or not author.stop_confirmed:
                    raise StoreError('fence')
                if original.state not in {'planned', 'ready', 'blocked', 'succeeded'}:
                    raise StoreError('completion_blocked', 'Reopen stopped work before revalidation')
            elif (original.current_attempt_id != identity or original.state != 'awaiting_review'
                    or original.fence != author.fence + 1 or author.state != 'awaiting_review'
                    or not author.stop_confirmed):
                raise StoreError('fence')
        self._lineage_guard(scope, record, deep=deep)
        if deep:
            self._reviewed(scope, record)
        return attempt, task, workflow

    def _verify(self, scope, record, executor, approvals, *, applied):
        directory = self.root / 'integrations' / record.id
        artifacts = [self.artifacts.get(scope, identity) for identity in record.artifact_hashes]
        by_task = {artifact.task_id: artifact for artifact in artifacts}
        workflow = self.store.workflow(scope, record.workflow_id)
        tasks = self._requirements(scope, record)
        results = {}
        original, seen = record, set()
        while original.previous_result_id is not None:
            if original.id in seen or len(seen) >= 64:
                raise StoreError('completion_changed')
            seen.add(original.id)
            original = self.get(scope, original.previous_result_id)
        baseline = self.root / 'integrations' / original.id / 'baseline'
        if set(approvals) != tasks:
            raise StoreError('approval_scope')
        for task_id in sorted(tasks):
            self._guard(scope, record, deep=True)
            task = self.store.task(scope, task_id)
            artifact = by_task.get(task_id, artifacts[0])
            merged = Path(record.source_root) if applied else directory / 'candidate'
            roots = {'baseline': self.artifacts.directory / artifact.id / 'baseline' if task_id in by_task else
                    baseline,
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

    def _resolution_inputs(self, scope, workflow_id, artifacts):
        return tuple(self.get(scope, row['id']) for row in self.store.db.execute(
            "SELECT id FROM integrations WHERE workflow_id=? AND state='conflict' ORDER BY rowid", (workflow_id,))
            if any(item.task_id == self.get(scope, row['id']).resolution_task_id
                and item.attempt_id in self.get(scope, row['id']).resolution_attempts for item in artifacts))

    def run(self, scope, attempt_id, fence, artifact_ids, review_ids, *, approval_hashes, reruns=0,
            previous_result_id=None, trust='trusted-local-process'):
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Integration cannot run in a SQLite transaction')
        store._host_guard()
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
            if record.state not in {'completed', 'conflict'}:
                raise StoreError('reconcile_required' if record.state != 'invalidated' else 'result_invalidated', current=record)
            return record
        store._execution_available(scope, attempt_id)
        attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        if any(item.state in ACTIVE and item.id != attempt.id for item in store.attempts(scope, workflow.id)):
            raise StoreError('incomplete_workflow', 'Every other executor must be stopped before integration')
        artifacts = [self.artifacts.get(scope, identity) for identity in artifact_ids]
        if (not artifacts or len(set(artifact_ids)) != len(artifact_ids) or len(set(review_ids)) != len(review_ids)
                or len({artifact.task_id for artifact in artifacts}) != len(artifacts)):
            raise StoreError('integration_scope')
        hashes = {artifact.id: artifact.patch_hash for artifact in artifacts}
        if task.input_artifacts != hashes or any(artifact.workflow_id != workflow.id for artifact in artifacts):
            raise StoreError('integration_scope')
        for artifact in artifacts:
            Inputs(store).require_artifact(scope, artifact)
        reviews = [self.reviews.get(scope, identity) for identity in review_ids]
        if {review.artifact_id for review in reviews} != set(hashes) or len(reviews) != len(hashes):
            raise StoreError('review_scope')
        sources = {self.artifacts.workspaces.get(scope, artifact.attempt_id).source_root for artifact in artifacts}
        if len(sources) != 1:
            raise StoreError('integration_scope')
        source = Path(sources.pop())
        registered_source(store, workflow.project_id, source)
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
        resolved = self._resolution_inputs(scope, workflow.id, artifacts)
        resolved_tasks = set().union(*(self._requirements(scope, item) for item in resolved))
        tasks = [item for item in store.tasks(scope, workflow.id) if required_task(item)]
        aggregates = self.aggregate_tasks(scope, workflow.id, {*author_attempts, workflow.root_task_id, task.id})
        root_task = store.task(scope, workflow.root_task_id)
        if root_task.id not in author_attempts and root_task.current_attempt_id is not None:
            raise StoreError('incomplete_workflow', 'An unselected root assignment cannot be completed as an aggregate')
        if set(approval_hashes) != set(author_attempts) | {workflow.root_task_id, task.id} | resolved_tasks | aggregates:
            raise StoreError('approval_scope')
        from .completions import Completions
        from .composition import Compositions

        if previous_result is None:
            artifacts, changes, conflicts = Compositions(store, self.root).merge(scope, artifacts, before)
        else:
            changes, conflicts = (), ()
        for artifact in artifacts:
            for change in artifact.changes:
                if root_task.permissions is None or not root_task.permissions.approved or not covers(root_task.permissions.write_scopes, (change.path,)):
                    raise StoreError('permission')
        record = IntegrationRecord(id=uuid4().hex, project_id=task.project_id, workflow_id=workflow.id,
            attempt_id=attempt_id, fence=fence, source_root=str(source), runtime_root=str(self.root), source_before=before,
            previous_result_id=previous_result_id,
            artifact_hashes=hashes, review_ids=tuple(review_ids), contracts={item.id: item.contract_hash for item in tasks},
            semantic_revisions={item.id: item.semantic_revision for item in tasks}, approval_hashes=approval_hashes, reruns=reruns,
            author_attempts=author_attempts, author_completions=Completions(store, self.root).bindings(scope, author_attempts, previous_result_id),
            changes=changes, conflict_paths=conflicts,
            resolved_conflicts=tuple(item.id for item in resolved),
            created_at=store.clock())
        restoring = self._restorable_reviews(scope, record)
        if any(item.state != 'succeeded'
                and item.id not in {*author_attempts, task.id, workflow.root_task_id, *resolved_tasks, *restoring, *aggregates} for item in tasks):
            raise StoreError('incomplete_workflow')
        self._guard(scope, record, deep=True)
        if conflicts:
            for identity, approval in approval_hashes.items():
                self.engine.oracles.resolve(approval, store.task(scope, identity).acceptance)
            return self._conflict(scope, record)
        directory = self.root / 'integrations' / record.id
        directory.mkdir(parents=True, mode=0o700)
        try:
            with locked(directory / 'coordinator.lock', create=True) as descriptor:
                info = os.fstat(descriptor)
                record = changed(record, coordinator_identity=(info.st_dev, info.st_ino))
                return self._execute(scope, record)
        finally:
            self._settle_source(scope, record.id)

    def _conflict(self, scope, record):
        store = self.store
        with store.transaction():
            attempt, task, workflow = self._guard(scope, record)
            store._claim_execution(scope, attempt.id, attempt.fence, 'integration')
            draft = store._new_task(workflow, TaskDraft(title='Resolve reviewed patch conflict',
                objective=f'Resolve integration {record.id} using both pinned artifact versions. '
                    'Preserve every original acceptance requirement and return a new patch for independent review.',
                parent_id=workflow.root_task_id, input_artifacts=record.artifact_hashes,
                base_snapshot_hash=record.source_before.subject_hash))
            blocker = None
            try:
                store._validate_graph(scope, workflow, draft)
            except StoreError as error:
                if error.code not in {'task_limit', 'children', 'depth'}:
                    raise
                blocker = error.code
            if blocker is None:
                store._save_task(draft)
                workflow = changed(workflow, revision=workflow.revision + 1, semantic_revision=workflow.semantic_revision + 1)
                store._save_workflow(workflow)
                store._event(scope, workflow, 'task_created', task=draft, revision=draft.revision, integration_id=record.id)
            record = changed(record, state='conflict', error='integration_conflict',
                resolution_task_id=draft.id if blocker is None else None, resolution_blocker=blocker)
            store.db.execute('INSERT INTO integrations VALUES (?,?,?,?,?)',
                (record.id, workflow.id, attempt.id, record.state, record.model_dump_json()))
            store._confirm_stopped(scope, attempt.id, attempt.fence, outcome='failed', expenditure_confirmed=True)
            stopped_task = store.task(scope, task.id)
            store._save_task(changed(stopped_task, blocked_reason=blocker or 'integration_conflict'))
            store._event(scope, workflow, 'integration_conflict', task=stopped_task, attempt=attempt,
                integration_id=record.id, resolution_task_id=record.resolution_task_id,
                blocker=blocker, conflict_paths=record.conflict_paths, artifact_hashes=record.artifact_hashes)
            return record

    def _execute(self, scope, record):
        store = self.store
        attempt_id, fence = record.attempt_id, record.fence
        attempt, task, workflow = self._guard(scope, record, deep=True)
        source, before = Path(record.source_root), record.source_before
        approval_hashes, hashes = record.approval_hashes, record.artifact_hashes
        artifacts = [self.artifacts.get(scope, identity) for identity in hashes]
        if record.previous_result_id is None:
            from .composition import Compositions

            artifacts, changes, conflicts = Compositions(store, self.root).merge(scope, artifacts, before)
            if changes != record.changes or conflicts:
                raise StoreError('artifact_lineage')
        sources = Sources(store)
        registered_source(store, workflow.project_id, source)
        root_ref = sources.prepare(source)
        with store.transaction():
            self._guard(scope, record)
            if store.db.execute("SELECT 1 FROM integrations WHERE workflow_id=? AND state IN ('preparing','verifying','applying','reconciling')",
                                (workflow.id,)).fetchone():
                raise StoreError('integration_busy')
            claim = sources.acquire(root_ref, owner_kind='integration', owner_id=record.id,
                coordinator_path=str(self.root / 'integrations' / record.id / 'coordinator.lock'),
                coordinator_identity=record.coordinator_identity, attempt_id=attempt_id, fence=fence)
            record = changed(record, source_claim_id=claim.id)
            store._claim_execution(scope, attempt_id, fence, 'integration')
            store.db.execute('INSERT INTO integrations VALUES (?,?,?,?,?)', (record.id, workflow.id, attempt_id, record.state, record.model_dump_json()))
            store._event(scope, workflow, 'integration_started', task=task, attempt=attempt, integration_id=record.id)
        directory = self.root / 'integrations' / record.id
        application = Application(directory, source, directory / 'baseline', directory / 'candidate')
        executor = CheckExecutor(store, scope, attempt_id, fence, source_claim_id=claim.id)
        apply_call = None
        try:
            self._source_guard(record)
            if capture(source, directory / 'baseline').subject_hash != before.subject_hash:
                raise StoreError('integration_conflict')
            shutil.copytree(directory / 'baseline', directory / 'candidate')
            providers = {change.path: artifact.id for artifact in artifacts for change in artifact.changes}
            for change in record.changes:
                target = directory / 'candidate' / change.path
                if change.after is None:
                    target.unlink()
                else:
                    data = read_regular(self.artifacts.directory / providers[change.path] / 'target' / change.path)
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
            if record.previous_result_id is None:
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
                for identity, review in self._restorable_reviews(scope, record).items():
                    original = store.task(scope, identity)
                    store._save_task(changed(original, state='succeeded', revision=original.revision + 1,
                        updated_at=store.clock()))
                    store._event(scope, workflow, 'verified_review_revalidated', task=original, integration_id=record.id,
                        review_id=review.id, evidence_id=review.verdict.evidence_id)
                for task_id, author_id in record.author_attempts.items():
                    original = store.task(scope, task_id)
                    if record.previous_result_id is None and task_id not in record.author_completions:
                        _succeed(store, scope, store._attempt(author_id), original, workflow,
                            evidence_id=record.applied_verdicts[task_id].evidence_id)
                    else:
                        store._save_task(changed(original, state='succeeded', revision=original.revision + 1, updated_at=store.clock()))
                        store._event(scope, workflow, 'verified_task_revalidated', task=original,
                            integration_id=record.id, evidence_id=record.applied_verdicts[task_id].evidence_id)
                _succeed(store, scope, attempt, task, workflow, evidence_id=record.applied_verdicts[task.id].evidence_id)
                for identity in self.aggregate_tasks(scope, workflow.id,
                        {*record.author_attempts, workflow.root_task_id, task.id}):
                    original = store.task(scope, identity)
                    store._save_task(changed(original, state='succeeded', revision=original.revision + 1,
                        updated_at=store.clock()))
                    store._event(scope, workflow, 'verified_aggregate_completed', task=original, integration_id=record.id,
                        evidence_id=record.applied_verdicts[identity].evidence_id)
                resolved_tasks = self._requirements(scope, record) if record.resolved_conflicts else set()
                for identity in resolved_tasks - set(record.author_attempts) - {task.id}:
                    original = store.task(scope, identity)
                    if original.state != 'succeeded':
                        if original.current_attempt_id is not None:
                            raise StoreError('fence')
                        store._save_task(changed(original, state='succeeded', revision=original.revision + 1,
                            updated_at=store.clock()))
                        store._event(scope, workflow, 'verified_task_resolved', task=original, integration_id=record.id,
                            resolved_conflicts=record.resolved_conflicts, evidence_id=record.applied_verdicts[identity].evidence_id)
                root_task = store.task(scope, workflow.root_task_id)
                if root_task.state != 'succeeded' and root_task.id not in record.author_attempts:
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
            effects_unknown = any(call.status in {'running', 'uncertain'} and call.tool != 'integrate'
                for call in Tools(store).calls(scope, workflow.id) if call.attempt_id == attempt_id)
            clean = False
            rollback_error = None
            if not effects_unknown:
                try:
                    clean = application.rollback(record.changes, lambda: self._source_guard(record))
                except (OSError, ValueError) as blocked:
                    rollback_error = getattr(blocked, 'code', 'rollback_conflict')
            if apply_call is not None:
                Tools(store).finish(scope, apply_call.id, status='failed' if clean else 'uncertain', error_class=type(error).__name__)
            record = self._save(scope, record, state='failed' if clean else 'reconciling', error='effects_unknown' if effects_unknown else rollback_error or getattr(error, 'code', type(error).__name__))
            if clean:
                try:
                    store.confirm_stopped(scope, attempt_id, fence, outcome='failed', expenditure_confirmed=True)
                except StoreError as stop_error:
                    if stop_error.code not in {'resource_busy', 'uncertain'}:
                        raise
            raise
