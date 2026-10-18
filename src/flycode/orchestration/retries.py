"""Exact rejected-review recovery under an explicit, cumulative owner budget."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from ..domain.pipeline import RetryCheckpoint
from ..domain.results import PatchArtifact, ReviewExecution, ReviewRecord
from ..domain.verification import CheckAttempt, EvidenceManifest
from ..verification.evidence import read_regular
from ..verification.snapshot import capture
from .artifacts import Artifacts
from .completions import Completions
from .composition import apply_versions, versions
from .integration_bindings import record_hash
from .review import Reviews
from .store import StoreError, changed


class Retries:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)

    @staticmethod
    def checkpoints(receipts):
        return [RetryCheckpoint.model_validate(value) for key, value in receipts.items() if key.startswith('retry:')]

    def metadata(self, scope, saved, *, retired=True):
        store = self.store
        artifact = store._load('artifacts', saved.artifact_id, PatchArtifact)
        task = store.task(scope, artifact.task_id)
        author = store._attempt(saved.author_attempt_id)
        review = store._load('reviews', saved.review_id, ReviewRecord)
        checker = store._attempt(saved.reviewer_attempt_id)
        binding = saved.review_binding
        execution = store._load('review_executions', checker.id, ReviewExecution)
        if (store._load('review_bindings', binding.id, type(binding)) != binding
                or binding.author_instance_id != author.instance_id
                or binding.project_id != task.project_id or binding.workflow_id != task.workflow_id
                or binding.runtime_root != str(self.root)
                or task.id != saved.task_id or artifact.task_id != task.id or artifact.workflow_id != task.workflow_id
                or artifact.project_id != task.project_id or artifact.attempt_id != author.id
                or artifact.fence != saved.author_fence or author.fence != saved.author_fence
                or author.task_id != task.id or author.workflow_id != task.workflow_id
                or author.state != ('interrupted' if retired else 'awaiting_review') or not author.stop_confirmed
                or artifact.semantic_revision != author.semantic_revision or artifact.contract_hash != author.contract_hash
                or record_hash(artifact) != saved.artifact_hash or review.artifact_id != artifact.id
                or review.patch_hash != artifact.patch_hash or record_hash(review) != saved.review_hash
                or review.status != 'changes_requested' or review.verdict.status != 'failed'
                or checker.id != review.reviewer_attempt_id or checker.state != 'failed' or not checker.stop_confirmed
                or checker.task_id != binding.task_id or checker.workflow_id != task.workflow_id
                or checker.fence != review.reviewer_fence or checker.contract_hash != binding.contract_hash
                or checker.semantic_revision != binding.semantic_revision or checker.instance_id == author.instance_id
                or binding.author_attempt_id != author.id or binding.author_fence != author.fence
                or binding.artifact_id != artifact.id or binding.patch_hash != artifact.patch_hash
                or binding.artifact_hash != saved.artifact_hash or binding.approval_hash != review.approval_hash
                or review.reviewer_contract_hash != checker.contract_hash or review.reruns != binding.reruns
                or execution.state != 'completed' or execution.review_id != review.id or execution.artifact_id != artifact.id
                or execution.patch_hash != artifact.patch_hash or execution.fence != checker.fence
                or execution.attempt_id != checker.id or execution.task_id != checker.task_id
                or execution.project_id != task.project_id or execution.workflow_id != task.workflow_id
                or execution.reruns != review.reruns
                or execution.runtime_root != str(self.root) or execution.approval_hash != review.approval_hash):
            raise StoreError('retry_changed')
        Completions(store, self.root)._settled(author.id)
        Completions(store, self.root)._settled(checker.id)
        return artifact, review

    def evidence(self, scope, saved, *, retired=True):
        if self.store.db.in_transaction:
            raise RuntimeError('Retry evidence cannot be inspected in a transaction')
        artifact, review = self.metadata(scope, saved, retired=retired)
        Artifacts(self.store, self.root).get(scope, artifact.id)
        engine = Reviews(self.store, self.root).engine
        if not engine.current(review.verdict, self.root / 'artifacts' / artifact.id / 'target',
                contract=self.store.task(scope, saved.task_id).acceptance, captured=True):
            raise StoreError('retry_evidence')
        return artifact, review

    def checkpoint(self, scope, attempt, *, deep=False):
        from .pipeline import Pipelines

        row = self.store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (attempt.workflow_id,)).fetchone()
        if row is None:
            return None
        recipe = Pipelines._recipe(row['data'])
        if recipe.version != 4:
            return None
        receipts = {row['stage']: json.loads(row['data']) for row in self.store.db.execute(
            'SELECT stage,data FROM pipeline_receipts WHERE pipeline_id=?', (recipe.id,))}
        saved = max((item for item in self.checkpoints(receipts) if item.task_id == attempt.task_id),
            key=lambda item: item.number, default=None)
        if saved is None or attempt.id == saved.author_attempt_id or attempt.fence <= saved.author_fence:
            return None
        artifact, review = self.evidence(scope, saved) if deep else self.metadata(scope, saved)
        if (artifact.prerequisites != attempt.prerequisites or artifact.semantic_revision != attempt.semantic_revision
                or artifact.contract_hash != attempt.contract_hash):
            raise StoreError('retry_changed')
        return saved, artifact, review

    def _block(self, scope, service, recipe, tasks, receipts, task, author, review, saved, reason):
        store = self.store
        with store.transaction():
            store._host_sql()
            workflow, current, now = service.guard_metadata(store, scope, recipe)
            if workflow.deadline_at is None or store.clock() >= workflow.deadline_at or store.clock() < workflow.started_at:
                raise StoreError('wall_deadline')
            if current != tasks or now != receipts or store._attempt(author.id) != author:
                raise StoreError('revision')
            if self.metadata(scope, saved, retired=False)[1] != review:
                raise StoreError('retry_changed')
            Completions(store, self.root)._settled(author.id)
            Completions(store, self.root)._settled(review.reviewer_attempt_id)
            self._fail(scope, workflow, task, author, review, reason)
        return True

    def _fail(self, scope, workflow, task, author, review, reason):
        store = self.store
        failed = changed(store._stop(task, outcome='failed'), state='failed', blocked_reason=reason,
            revision=task.revision + 1, updated_at=store.clock())
        store._save_attempt(changed(store._attempt(author.id), state='failed'))
        store._save_task(failed)
        store._save_workflow(changed(workflow, revision=workflow.revision + 1))
        store._event(scope, workflow, 'pipeline_retry_blocked', task=failed, reason=reason, review_id=review.id)

    def advance(self, scope, service, recipe, tasks, receipts):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        for branch in recipe.branches:
            policy = recipe.retry_policies.get(branch.writer_id)
            task, reviewer = tasks[branch.writer_id], tasks[branch.reviewer_id]
            if policy is None or task.state != 'awaiting_review' or reviewer.state != 'failed':
                continue
            if service._task_blocked(recipe, tasks, task.id, receipts):
                continue
            previous = self.checkpoints(receipts)
            number = 1 + sum(item.task_id == task.id for item in previous)
            stage = service._review_stage(recipe, branch)
            old = receipts.get(stage)
            if old is None:
                raise StoreError('retry_changed')
            binding = service._receipt_binding(old['binding'])
            rows = store.db.execute('SELECT id FROM reviews WHERE attempt_id IN (SELECT id FROM attempts WHERE task_id=?) '
                "AND json_extract(data,'$.reviewer_fence')=?", (reviewer.id, reviewer.fence - 1)).fetchall()
            if len(rows) != 1:
                raise StoreError('retry_changed')
            review = store._load('reviews', rows[0]['id'], ReviewRecord)
            if review.status != 'changes_requested':
                continue
            artifact, author, sequence = service._artifact(scope, recipe, tasks, deep=True, writer_id=task.id)
            saved = RetryCheckpoint(id=uuid4().hex, pipeline_id=recipe.id, task_id=task.id, number=min(number, policy.max_retries),
                artifact_id=artifact.id, artifact_hash=record_hash(artifact), review_id=review.id, review_hash=record_hash(review),
                author_attempt_id=author.id, author_fence=author.fence, reviewer_attempt_id=review.reviewer_attempt_id,
                review_binding=binding, artifact_sequence=sequence, reset_semantic_revision=reviewer.semantic_revision + 1,
                created_at=store.clock())
            self.evidence(scope, saved, retired=False)
            if number > policy.max_retries:
                return self._block(scope, service, recipe, tasks, receipts, task, author, review, saved, 'retry_exhausted')
            if capture(Path(recipe.source_root)).subject_hash != recipe.source_hash:
                raise StoreError('snapshot_changed')
            with store.transaction():
                store._host_sql()
                workflow, current, now = service.guard_metadata(store, scope, recipe)
                key = 'retry:' + task.id + ':' + str(number)
                if key in now:
                    return True
                if current != tasks or now != receipts or self.metadata(scope, saved, retired=False) != (artifact, review):
                    raise StoreError('revision')
                if (workflow.deadline_at is None or store.clock() >= workflow.deadline_at or store.clock() < workflow.started_at):
                    raise StoreError('wall_deadline')
                attempts, ledger = store.attempts(scope, workflow.id), store.budget(scope, workflow.id)
                if (sum(attempt.task_id == task.id for attempt in attempts) >= workflow.budget.max_attempts
                        or store._remaining_reservation(workflow, task, ledger, attempts) != policy.writer_budget
                        or store._remaining_reservation(workflow, reviewer, ledger, attempts) != policy.reviewer_budget):
                    self._fail(scope, workflow, task, author, review, 'retry_budget')
                    return True
                ready = changed(store._stop(task), state='ready', blocked_reason=None, queued_at=store.clock(),
                    revision=task.revision + 1, updated_at=store.clock())
                reset = changed(reviewer, state='planned', input_artifacts={}, blocked_reason=None,
                    semantic_revision=saved.reset_semantic_revision, revision=reviewer.revision + 1, updated_at=store.clock())
                store._save_task(ready)
                store._save_task(reset)
                store.db.execute('UPDATE review_bindings SET current=0 WHERE task_id=?', (reviewer.id,))
                store.db.execute('DELETE FROM pipeline_receipts WHERE pipeline_id=? AND stage=?', (recipe.id, stage))
                store.db.execute('INSERT INTO pipeline_receipts VALUES (?,?,?)', (recipe.id, key, saved.model_dump_json()))
                store._save_workflow(changed(workflow, revision=workflow.revision + 1))
                store._event(scope, workflow, 'pipeline_retry_ready', task=ready, checkpoint_id=saved.id,
                    artifact_id=artifact.id, review_id=review.id, number=number)
            return True
        return False

    def context(self, scope, attempt):
        checkpoint = self.checkpoint(scope, attempt, deep=True)
        if checkpoint is None:
            return ''
        _, artifact, review = checkpoint
        raw = read_regular(self.root / 'artifacts' / artifact.id / 'run-result.json', limit=65536)
        if hashlib.sha256(raw).hexdigest() != artifact.result_hash:
            raise StoreError('retry_changed')
        result = json.loads(raw)
        result.pop('metrics', None)
        result['episode'].pop('task', None)
        result['episode'] = {key: value for key, value in result['episode'].items() if value not in ({}, [])}
        verdict = review.verdict
        feedback = {'id': review.id, 'status': review.status, 'verdict': {
            'status': verdict.status, 'checks': verdict.checks, 'reasons': verdict.reasons,
            'cases': [case.model_dump(mode='json', include={'id', 'outcome', 'attempt', 'flaky', 'diagnostics', 'check_id'})
                for case in verdict.cases if verdict.checks.get(case.check_id) != 'passed']}}
        directory = self.root / 'verification' / verdict.evidence_id
        raw = read_regular(directory / 'manifest.json', limit=65536)
        if hashlib.sha256(raw).hexdigest() != verdict.evidence_hash:
            raise StoreError('retry_evidence')
        manifest = EvidenceManifest.model_validate_json(raw)
        feedback['outputs'] = []
        for invocation in manifest.invocations:
            item = CheckAttempt.model_validate(invocation)
            if verdict.checks.get(item.check_id) != 'failed':
                continue
            name = f'attempt-{item.id}/junit.xml'
            if name not in manifest.artifacts or '..' in Path(name).parts:
                raise StoreError('retry_evidence')
            raw = read_regular(directory / name, limit=65536)
            if (hashlib.sha256(raw).hexdigest() != manifest.artifacts[name]
                    or b'\0' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper()):
                raise StoreError('retry_evidence')
            report = ET.fromstring(raw)
            failures = [{'case': case.get('classname', '') + '::' + case.get('name', ''),
                'diagnostic': failure.text or failure.get('message', '')} for case in report.iter('testcase')
                for failure in case if failure.tag in {'failure', 'error'}]
            feedback['outputs'].append({'check_id': item.check_id, 'attempt': item.number, 'failures': failures})
        context = '\nImmutable rejected attempt data (no additional authority):\n' + json.dumps(
            {'result': result, 'review': feedback}, ensure_ascii=False, separators=(',', ':'))
        if len(context.encode()) > attempt.profile_snapshot.context_limit * 2:
            raise StoreError('retry_context')
        return context

    def configure(self, scope, attempt, spec):
        context = self.context(scope, attempt)
        return spec.model_copy(update={'task': spec.task + context}) if context else spec

    def restore(self, scope, attempt, repository):
        checkpoint = self.checkpoint(scope, attempt, deep=True)
        if checkpoint is None:
            return
        _, artifact, _ = checkpoint
        expected = apply_versions(versions(capture(repository)), [artifact])
        for change in artifact.changes:
            target = repository / change.path
            if change.after is None:
                target.unlink(missing_ok=True)
            else:
                raw = read_regular(self.root / 'artifacts' / artifact.id / 'target' / change.path)
                if hashlib.sha256(raw).hexdigest() != change.after.sha256:
                    raise StoreError('retry_changed')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(change.after.mode)
        if versions(capture(repository)) != expected:
            raise StoreError('retry_changed')
        return True

    def require_artifact(self, scope, artifact):
        saved = artifact.retry
        row = self.store.db.execute('SELECT data FROM pipeline_receipts WHERE pipeline_id=? AND stage=?',
            (saved.pipeline_id, 'retry:' + saved.task_id + ':' + str(saved.number))).fetchone()
        if (row is None or RetryCheckpoint.model_validate_json(row['data']) != saved
                or artifact.task_id != saved.task_id or artifact.fence <= saved.author_fence or artifact.id == saved.artifact_id):
            raise StoreError('retry_changed')
        original, _ = self.evidence(scope, saved)
        if (original.project_id != artifact.project_id or original.workflow_id != artifact.workflow_id
                or original.contract_hash != artifact.contract_hash or original.semantic_revision != artifact.semantic_revision
                or original.base.subject_hash != artifact.base.subject_hash or original.prerequisites != artifact.prerequisites):
            raise StoreError('retry_changed')
