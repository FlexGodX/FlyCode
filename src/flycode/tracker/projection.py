"""Presentation values are derived, never a second mutable task lifecycle."""

from pydantic import Field

from ..domain.orchestration import (
    AgentAttempt,
    AgentInstance,
    CompletionInput,
    QueueEvent,
    QueueModel,
    WorkflowSpec,
    WorkItem,
)

COLUMNS = {'planned': 'drafts', 'ready': 'queue', 'assigned': 'in_progress', 'running': 'in_progress',
           'awaiting_review': 'review', 'succeeded': 'done', 'blocked': 'blocked', 'retry_wait': 'blocked',
           'failed': 'failed', 'cancelled': 'cancelled'}


class CompletionView(QueueModel):
    id: str
    artifact_id: str
    review_id: str
    current: bool


class IntegrationView(QueueModel):
    id: str
    state: str
    current: bool
    error: str | None


class ReviewView(QueueModel):
    id: str
    status: str
    evidence_id: str


class TaskLineage(QueueModel):
    artifact_id: str | None = None
    inputs: dict[str, CompletionInput] = Field(default_factory=dict)
    continuation_id: str | None = None
    retry_number: int = 0
    retry_limit: int = 0
    pipeline_id: str | None = None
    graph_revision: int | None = None


class ChildWait(QueueModel):
    checkpoint_id: str
    children: tuple[str, ...]
    pending: tuple[str, ...]


class TaskCard(QueueModel):
    task: WorkItem
    column: str
    attempt: AgentAttempt | None = None
    last_event: QueueEvent | None = None
    blockers: tuple[str, ...] = ()
    completion: CompletionView | None = None
    integration: IntegrationView | None = None
    review: ReviewView | None = None
    lineage: TaskLineage = Field(default_factory=TaskLineage)
    wait: ChildWait | None = None
    last_action: QueueEvent | None = None


class AgentCard(QueueModel):
    instance: AgentInstance
    task: WorkItem | None = None
    attempt: AgentAttempt | None = None
    card: TaskCard | None = None


def task_card(task: WorkItem, workflow: WorkflowSpec, tasks: list[WorkItem], attempt: AgentAttempt | None,
              event: QueueEvent | None) -> TaskCard:
    blockers = []
    if task.blocked_reason:
        blockers.append(task.blocked_reason)
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
        states = {item.id: item.state for item in tasks if item.retired_by_result is None}
        if any(states.get(dependency) != 'succeeded' for dependency in task.dependencies):
            blockers.append('dependency')
    if attempt and task.current_attempt_id == attempt.id and attempt.state in {'stopping', 'reconciling'}:
        blockers.append('stop_unconfirmed')
    return TaskCard(task=task, column=COLUMNS[task.state], attempt=attempt, last_event=event, blockers=tuple(blockers))


class TaskPage(QueueModel):
    items: list[TaskCard]
    next_cursor: int | None = None
    cursor: int


class TaskSnapshot(TaskPage):
    # cursor bounds current board state; replay_after bounds delivered history.
    replay_after: int


class TaskEventPage(QueueModel):
    items: list[QueueEvent]
    next_cursor: int | None = None
    cursor: int
