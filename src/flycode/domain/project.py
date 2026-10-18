"""Host-owned physical project identity, independent of local UI selectors."""
from typing import Literal

from pydantic import Field, model_validator

from .orchestration import QueueModel, TaskState
from .source import SourceRoot


class CanonicalProject(QueueModel):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    root: SourceRoot
    revision: int = Field(default=1, ge=1)
    state: Literal['active', 'revoked'] = 'active'
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float
    revoked_at: float | None = None

    @model_validator(mode='after')
    def revocation(self):
        if (self.state == 'revoked') != (self.revoked_at is not None):
            raise ValueError('Revocation state requires its timestamp')
        return self


class ProjectStopGrant(QueueModel):
    version: Literal['project-stop-grant-v1'] = 'project-stop-grant-v1'
    actor_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_revision: int = Field(ge=1)
    granted_at: float
    capability: Literal['stop'] = 'stop'


class TaskStopReceipt(QueueModel):
    task_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=1)
    state: TaskState
    paused: bool


class TaskStopPage(QueueModel):
    items: list[TaskStopReceipt] = Field(max_length=100)
    next_cursor: int | None = Field(default=None, ge=1)
