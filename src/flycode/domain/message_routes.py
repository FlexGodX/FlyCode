"""Immutable owner authority for directed logical task communication."""

from typing import Literal

from pydantic import Field, model_validator

from .orchestration import QueueModel
from .verification_control import CatalogueID


class MessageRouteEdge(QueueModel):
    sender_task_id: CatalogueID
    recipient_task_id: CatalogueID

    @model_validator(mode='after')
    def distinct_tasks(self):
        if self.sender_task_id == self.recipient_task_id:
            raise ValueError('A route requires two different tasks')
        return self


class MessageTaskPin(QueueModel):
    semantic_revision: int = Field(strict=True, ge=1)
    contract_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class MessageRouteGrant(QueueModel):
    version: Literal[1] = 1
    id: CatalogueID
    owner_id: str
    project_id: CatalogueID
    workflow_id: CatalogueID
    workflow_revision: int = Field(strict=True, ge=1)
    workflow_semantic_revision: int = Field(strict=True, ge=1)
    pipeline_id: CatalogueID
    graph_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    task_pins: dict[CatalogueID, MessageTaskPin] = Field(min_length=1, max_length=64)
    task_revisions: dict[CatalogueID, int] = Field(min_length=1, max_length=64)
    edges: tuple[MessageRouteEdge, ...] = Field(max_length=192)
    previous_id: CatalogueID | None
    created_at: float


class MessageGrantPin(QueueModel):
    id: CatalogueID
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ResolvedMessageRoutes(QueueModel):
    grant: MessageGrantPin | None = None
    recipients: tuple[CatalogueID, ...] = Field(default=(), max_length=3)
