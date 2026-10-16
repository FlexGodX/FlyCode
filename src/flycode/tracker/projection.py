"""Presentation values are derived, never a second mutable task lifecycle."""

from ..domain.orchestration import (
    AgentAttempt,
    AgentInstance,
    QueueEvent,
    QueueModel,
    WorkflowSpec,
    WorkItem,
)

COLUMNS = {'planned': 'drafts', 'ready': 'queue', 'assigned': 'in_progress', 'running': 'in_progress',
           'awaiting_review': 'review', 'succeeded': 'done', 'blocked': 'blocked', 'retry_wait': 'blocked',
           'failed': 'failed', 'cancelled': 'cancelled'}


class TaskCard(QueueModel):
    task: WorkItem
    column: str
    attempt: AgentAttempt | None = None
    last_event: QueueEvent | None = None
    blockers: tuple[str, ...] = ()


class AgentCard(QueueModel):
    instance: AgentInstance
    task: WorkItem | None = None
    attempt: AgentAttempt | None = None


def task_card(task: WorkItem, workflow: WorkflowSpec, tasks: list[WorkItem], attempt: AgentAttempt | None,
              event: QueueEvent | None) -> TaskCard:
    blockers = []
    if task.paused or workflow.state in {'paused', 'pausing'}:
        blockers.append('paused')
    if task.state == 'ready' and not workflow.active_slot:
        blockers.append('root_not_active')
    if task.state in {'planned', 'ready'}:
        if not task.objective.strip():
            blockers.append('missing_objective')
        if task.acceptance is None:
            blockers.append('missing_criteria')
        if task.permissions is None or not task.permissions.approved:
            blockers.append('permission')
        if task.budget is None or workflow.budget is None:
            blockers.append('missing_budget')
        states = {item.id: item.state for item in tasks}
        if any(states.get(dependency) != 'succeeded' for dependency in task.dependencies):
            blockers.append('dependency')
    if attempt and task.current_attempt_id == attempt.id and attempt.state in {'stopping', 'reconciling'}:
        blockers.append('stop_unconfirmed')
    return TaskCard(task=task, column=COLUMNS[task.state], attempt=attempt, last_event=event, blockers=tuple(blockers))
