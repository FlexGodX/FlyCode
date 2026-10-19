"""Scoped result facts; deep evidence inspection never holds a SQL transaction."""

import json

from ..domain.orchestration import QueueEvent
from ..domain.results import (
    IntegrationRecord,
    PatchArtifact,
    ReviewRecord,
    TaskCompletion,
)
from ..orchestration.completions import Completions
from ..orchestration.integration import Integration
from ..orchestration.pipeline import Pipelines
from ..orchestration.retries import Retries
from ..orchestration.store import StoreError, changed
from .projection import (
    ChildWait,
    CompletionView,
    IntegrationView,
    ReviewView,
    TaskLineage,
)


class ResultViews:
    def __init__(self, store):
        self.store = store
        self.facts = {}

    def capture(self, scope, card):
        store, task = self.store, card.task
        if not store.db.in_transaction:
            raise RuntimeError('Result metadata requires the card snapshot transaction')
        workflow = store.workflow(scope, task.workflow_id)
        blockers = list(card.blockers)
        completion = integration = review = wait = proof = record = None
        eligible = True
        inputs = card.attempt.prerequisites if card.attempt else {}
        lineage = TaskLineage(inputs=inputs)
        try:
            proof = store._load('task_completions', task.completion_id, TaskCompletion) if task.completion_id else None
            checked = None
            artifact_id = proof.artifact_id if proof else None
            if task.kind == 'review' and card.attempt:
                row = store.db.execute('SELECT data FROM reviews WHERE attempt_id=? ORDER BY rowid DESC LIMIT 1', (card.attempt.id,)).fetchone()
                if row:
                    checked = ReviewRecord.model_validate_json(row['data'])
                    artifact_id = checked.artifact_id
            if artifact_id is None and card.attempt is not None:
                row = store.db.execute('SELECT id FROM artifacts WHERE attempt_id=?', (card.attempt.id,)).fetchone()
                artifact_id = row['id'] if row else None
            if artifact_id:
                artifact = store._load('artifacts', artifact_id, PatchArtifact)
                store.task(scope, artifact.task_id)
                if ((artifact.task_id != task.id and checked is None) or artifact.workflow_id != task.workflow_id or artifact.project_id != task.project_id):
                    raise StoreError('scope')
                lineage = changed(lineage, artifact_id=artifact.id, inputs=artifact.prerequisites,
                    continuation_id=artifact.continuation.id if artifact.continuation else None,
                    retry_number=artifact.retry.number if artifact.retry else 0)
                if checked is None:
                    row = store.db.execute('SELECT data FROM reviews WHERE artifact_id=? ORDER BY rowid DESC LIMIT 1', (artifact.id,)).fetchone()
                    checked = ReviewRecord.model_validate_json(row['data']) if row else None
                if checked:
                    checker = store._attempt(checked.reviewer_attempt_id)
                    store.task(scope, checker.task_id)
                    if checker.workflow_id != task.workflow_id:
                        raise StoreError('scope')
                    review = ReviewView(id=checked.id, status=checked.status, evidence_id=checked.verdict.evidence_id)
            if proof:
                if proof.task_id != task.id or proof.workflow_id != task.workflow_id or proof.project_id != task.project_id:
                    raise StoreError('scope')
                completion = CompletionView(id=proof.id, artifact_id=proof.artifact_id, review_id=proof.review_id,
                    current=False)
            row = store.db.execute('SELECT data FROM integrations WHERE workflow_id=? ORDER BY rowid DESC LIMIT 1', (workflow.id,)).fetchone()
            if row:
                record = IntegrationRecord.model_validate_json(row['data'])
                if record.project_id != task.project_id:
                    raise StoreError('scope')
                integration = IntegrationView(id=record.id, state=record.state, current=False, error=record.error)
                if record.error:
                    blockers.append(record.error)
            row = store.db.execute('SELECT data FROM pipelines WHERE workflow_id=?', (workflow.id,)).fetchone()
            if row:
                recipe = Pipelines.effective(store, row['data'])
                if recipe.project_id != task.project_id:
                    raise StoreError('scope')
                lineage = changed(lineage, pipeline_id=recipe.id, graph_revision=getattr(recipe, 'graph_revision', 0))
                if workflow.state in {'draft', 'running'} and task.state not in {'succeeded', 'failed', 'cancelled'}:
                    tasks = {item.id: item for item in store.tasks(scope, workflow.id)}
                    receipts = Pipelines.receipts(store, recipe) if recipe.version == 4 else {}
                    if Pipelines._task_blocked(recipe, tasks, task.id, receipts):
                        blockers.append('pipeline_branch_paused')
                    if recipe.version == 4:
                        Pipelines.guard_metadata(store, scope, recipe)
                if recipe.version == 4:
                    receipts = Pipelines.receipts(store, recipe)
                    writer_id = next((branch.writer_id for branch in recipe.branches if task.id in {branch.writer_id, branch.reviewer_id}), task.id)
                    policy = recipe.retry_policies.get(writer_id)
                    checkpoints = [item.number for item in Retries.checkpoints(receipts) if item.task_id == writer_id]
                    lineage = changed(lineage, retry_limit=policy.max_retries if policy else 0,
                        retry_number=max((lineage.retry_number, *checkpoints)))
                    checkpoint = receipts.get('wait:' + task.id)
                    if checkpoint:
                        lineage = changed(lineage, continuation_id=checkpoint['id'])
                        if 'writer:' + task.id not in receipts:
                            children = tuple(branch.writer_id for branch in recipe.branches if branch.writer_id != task.id)
                            pending = tuple(identity for identity in children if store.task(scope, identity).state != 'succeeded')
                            wait = ChildWait(checkpoint_id=checkpoint['id'], children=children, pending=pending)
        except (OSError, ValueError) as error:
            if isinstance(error, StoreError) and error.code == 'scope':
                raise
            eligible = False
            blockers.append(getattr(error, 'code', 'evidence_unavailable'))
            completion = changed(completion, current=False) if completion else None
            integration = changed(integration, current=False) if integration else None
        if task.state not in {'succeeded', 'failed', 'cancelled'} and workflow.deadline_at is not None and store.clock() >= workflow.deadline_at:
            blockers.append('wall_deadline')
        action = None
        for row in store.db.execute('SELECT sequence,data FROM events WHERE workflow_id=? ORDER BY sequence DESC', (workflow.id,)):
            event = json.loads(row['data'])
            if event['kind'] != 'attempt_heartbeat' and (event['task_id'] == task.id or event['kind'] == 'pipeline_replanned'):
                action = QueueEvent(sequence=row['sequence'], **{**event, 'data': {}})
                break
        self.facts[task.id] = (workflow, proof, record, eligible)
        return changed(card, completion=completion, integration=integration, review=review, lineage=lineage, wait=wait,
            blockers=tuple(dict.fromkeys(blockers)), last_action=action)

    def card(self, scope, card):
        store, task = self.store, card.task
        if store.db.in_transaction:
            raise RuntimeError('Result evidence inspection cannot hold a SQL transaction')
        workflow, proof, record, eligible = self.facts[task.id]
        completion, integration = card.completion, card.integration
        blockers = list(card.blockers)
        try:
            if eligible and completion:
                completion = changed(completion, current=Completions(store, proof.runtime_root.path).current(scope, proof.id))
                if not completion.current:
                    blockers.append('completion_invalidated')
            if eligible and integration:
                integration = changed(integration, current=bool(record.runtime_root and Integration(store, record.runtime_root).current(scope, record.id)))
                if record.state == 'completed' and not integration.current:
                    blockers.append('result_invalidated')
            if (store.task(scope, task.id) != task or store.workflow(scope, workflow.id) != workflow
                    or (record and store._load('integrations', record.id, IntegrationRecord) != record)):
                raise StoreError('revision')
        except (OSError, ValueError) as error:
            if isinstance(error, StoreError) and error.code == 'scope':
                raise
            blockers.append(getattr(error, 'code', 'evidence_unavailable'))
            completion = changed(completion, current=False) if completion else None
            integration = changed(integration, current=False) if integration else None
        return changed(card, completion=completion, integration=integration, blockers=tuple(dict.fromkeys(blockers)))
