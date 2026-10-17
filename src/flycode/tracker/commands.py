"""Validate tracker input using the dispatcher's canonical command schema."""

from pydantic import Field, field_validator

from ..domain.orchestration import (
    AccessScope,
    QueueEvent,
    QueueModel,
    TaskCommand,
    WorkItem,
)
from ..orchestration.store import Store


def execute(store: Store, scope: AccessScope, task_id: str, data: dict) -> WorkItem:
    return store.command(scope, task_id, TaskCommand.model_validate(data))


class CommentCommand(QueueModel):
    text: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Comment cannot be blank')
        return value


def comment(store: Store, scope: AccessScope, task_id: str, data: CommentCommand) -> QueueEvent:
    with store.transaction():
        task = store.task(scope, task_id)
        store._scope(scope, task.project_id, ('owner',))
        request = {'task_comment': task_id, 'text': data.text}
        found, result, digest = store._replay(scope, data.idempotency_key, request)
        if found:
            return QueueEvent.model_validate(result)
        workflow = store.workflow(scope, task.workflow_id)
        store._event(scope, workflow, 'task_comment', task=task, text=data.text, task_revision=task.revision)
        event = store.last_event(scope, workflow.id, task.id)
        store._remember(scope, data.idempotency_key, task.project_id, digest, event)
        return event
