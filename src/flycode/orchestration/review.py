"""Independent registered review of immutable worker output; no approval booleans."""

from pathlib import Path
from uuid import uuid4

from ..domain.results import ReviewRecord
from ..domain.verification import SubjectRef
from ..verification.engine import VerificationEngine
from ..verification.plan import PlanBuilder, contract_hash
from ..verification.snapshot import capture
from .artifacts import Artifacts
from .store import Store, StoreError
from .verification import CheckExecutor, _succeed, accepted


class Reviews:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.artifacts = Artifacts(store, root)
        self.engine = VerificationEngine(self.root / 'verification')

    def get(self, scope, identity) -> ReviewRecord:
        record = self.store._load('reviews', identity, ReviewRecord)
        self.store.task(scope, self.store._attempt(record.reviewer_attempt_id).task_id)
        return record

    def run(self, scope, attempt_id, fence, artifact_id, *, approval_hash, reruns=0, trust='trusted-local-process') -> ReviewRecord:
        store = self.store
        store._role(scope, ('runtime',))
        if store.db.in_transaction:
            raise RuntimeError('Review cannot run in a SQLite transaction')
        if trust != 'trusted-local-process':
            raise StoreError('unsupported_sandbox')
        previous = store.db.execute('SELECT id FROM reviews WHERE attempt_id=?', (attempt_id,)).fetchone()
        if previous:
            record = self.get(scope, previous['id'])
            if (record.reviewer_fence != fence or record.artifact_id != artifact_id
                    or record.approval_hash != approval_hash or record.reruns != reruns):
                raise StoreError('idempotency')
            return record
        attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        artifact = self.artifacts.get(scope, artifact_id)
        author = store._attempt(artifact.attempt_id)
        original = store.task(scope, artifact.task_id)
        if (task.kind != 'review' or attempt.profile_snapshot.role != 'reviewer' or attempt.instance_id == author.instance_id
                or task.workflow_id != artifact.workflow_id or task.input_artifacts.get(artifact_id) != artifact.patch_hash
                or task.acceptance is None or original.acceptance is None
                or contract_hash(task.acceptance) != contract_hash(original.acceptance)):
            raise StoreError('review_scope')
        if store.db.execute('SELECT 1 FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone():
            raise StoreError('worker_exists', 'A verification service cannot share a worker session')
        executor = CheckExecutor(store, scope, attempt_id, fence)
        engine = VerificationEngine(self.engine.data_dir, invocation_executor=executor.execute)
        approved = engine.oracles.read(approval_hash)
        directory = self.artifacts.directory / artifact_id
        roots = {'baseline': directory / 'baseline', **{stage: directory / 'target' for stage in ('target', 'regression', 'integration')}}
        ref = SubjectRef(project_id=task.project_id, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt.id)
        snapshots = {stage: capture(root, ref=ref) for stage, root in roots.items()}
        plan = PlanBuilder().build(snapshots['integration'], task.acceptance, approved.checks,
            subjects=snapshots, approval_hash=approval_hash, reruns=reruns)
        with store.transaction():
            store._claim_execution(scope, attempt_id, fence, 'review')
        verdict = engine.verify_plan(plan, roots, should_stop=executor.should_stop)
        self.artifacts.get(scope, artifact_id)
        passed = accepted(engine, verdict, roots['integration'], task.acceptance)
        stop_reason = None
        with store.transaction():
            store.task(scope, task.id)
            try:
                current_attempt, current_task, current_workflow = store._owned(scope, attempt_id, fence, running=True)
                if current_task.paused or current_workflow.state != 'running' or store._counter('queue_paused'):
                    raise StoreError('paused')
            except StoreError as error:
                if error.code not in {'fence', 'paused', 'wall_deadline'}:
                    raise
                passed, stop_reason = False, error.code
            record = ReviewRecord(id=uuid4().hex, artifact_id=artifact_id, patch_hash=artifact.patch_hash,
                reviewer_attempt_id=attempt_id, reviewer_fence=fence, reviewer_contract_hash=task.contract_hash,
                approval_hash=approval_hash, reruns=reruns, status='approved' if passed else 'inconclusive' if stop_reason else
                    'changes_requested' if verdict.status == 'failed' else 'inconclusive', verdict=verdict, created_at=store.clock())
            store.db.execute('INSERT INTO reviews VALUES (?,?,?,?)', (record.id, artifact_id, attempt_id, record.model_dump_json()))
            if passed:
                _succeed(store, scope, current_attempt, current_task, current_workflow, evidence_id=verdict.evidence_id)
            store._event(scope, workflow, 'review_completed', task=task, attempt=attempt, review_id=record.id,
                artifact_id=artifact.id, patch_hash=artifact.patch_hash, status=record.status,
                evidence_id=verdict.evidence_id, stop_reason=stop_reason)
        if not passed:
            try:
                current_task = store.task(scope, task.id)
                current_workflow = store.workflow(scope, workflow.id)
                outcome = 'retry_wait' if current_task.paused or current_workflow.state == 'pausing' else 'failed'
                store.confirm_stopped(scope, attempt_id, fence, outcome=outcome, expenditure_confirmed=True)
            except StoreError as error:
                if error.code not in {'resource_busy', 'uncertain'}:
                    raise
        return record
