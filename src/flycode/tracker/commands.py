"""Validate tracker input using the dispatcher's canonical command schema."""

from ..domain.orchestration import AccessScope, TaskCommand, WorkItem
from ..orchestration.store import Store


def execute(store: Store, scope: AccessScope, task_id: str, data: dict) -> WorkItem:
    return store.command(scope, task_id, TaskCommand.model_validate(data))
