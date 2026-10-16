"""Tracker access to the same canonical objects used by the dispatcher."""

from ..domain.orchestration import AccessScope, WorkflowDraft
from ..orchestration.store import Store
from .commands import execute
from .projection import AgentCard, TaskCard, task_card


class TrackerService:
    def __init__(self, store: Store):
        self.store = store

    def create(self, scope: AccessScope, draft: WorkflowDraft, *, key: str):
        return self.store.create_workflow(scope, draft, key=key)

    def command(self, scope: AccessScope, task_id: str, data: dict):
        return execute(self.store, scope, task_id, data)

    def task(self, scope: AccessScope, task_id: str) -> TaskCard:
        self.store.refresh_results(scope, self.store.task(scope, task_id).workflow_id)
        return self._card(scope, task_id)

    def _card(self, scope: AccessScope, task_id: str) -> TaskCard:
        task = self.store.task(scope, task_id)
        workflow = self.store.workflow(scope, task.workflow_id)
        attempts = [attempt for attempt in self.store.attempts(scope, workflow.id) if attempt.task_id == task.id]
        current = next((a for a in attempts if a.id == task.current_attempt_id), None)
        return task_card(task, workflow, self.store.tasks(scope, workflow.id),
                         current or (attempts[-1] if attempts else None), self.store.last_event(scope, workflow.id, task.id))

    def board(self, scope: AccessScope, workflow_id: str, *, archived: bool = False) -> list[TaskCard]:
        self.store.refresh_results(scope, workflow_id)
        return [self._card(scope, task.id) for task in self.store.tasks(scope, workflow_id) if archived or not task.archived]

    def agents(self, scope: AccessScope) -> list[AgentCard]:
        self.store.refresh_results(scope)
        result = []
        for instance in self.store.instances(scope):
            attempt = self.store._attempt(instance.current_attempt_id) if instance.current_attempt_id else None
            task = self.store.task(scope, attempt.task_id) if attempt else None
            result.append(AgentCard(instance=instance, task=task, attempt=attempt))
        return result
