"""Bounded public owner commands; paths and execution authority stay server-side."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from ..domain.agent_requests import (
    AgentRequestCredential,
    AgentTaskRequest,
    AgentTaskRequestReceipt,
)
from ..domain.message_routes import MessageRouteEdge
from ..domain.orchestration import BudgetAmount, QueueModel, TaskDraft
from ..domain.pipeline import RetryPolicy
from ..domain.verification_control import CatalogueID

Revision = Annotated[int, Field(strict=True, ge=1)]
CommandKey = Annotated[str, Field(min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f]+$')]


class ProfileRef(QueueModel):
    id: CatalogueID
    version: Revision


class WorkerPreparation(QueueModel):
    expected_revision: Revision
    expected_binding_id: CatalogueID | None
    brain: str = Field(min_length=1, max_length=100)
    seed: int = Field(default=0, strict=True, ge=0, le=2**32 - 1)


class TaskChange(QueueModel):
    objective: str | None = Field(default=None, min_length=1, max_length=20000)
    dependencies: tuple[str, ...] | None = Field(default=None, max_length=64)


class NewBranch(QueueModel):
    writer: TaskDraft
    reviewer: TaskDraft
    template_writer_id: CatalogueID
    profile: ProfileRef


class OwnerBase(QueueModel):
    version: Literal['owner-command-v1'] = 'owner-command-v1'
    project_id: CatalogueID
    project_revision: Revision


class OwnerPreview(OwnerBase):
    operation: Literal['preview']
    workflow_id: CatalogueID


class OwnerPrepare(OwnerBase):
    operation: Literal['prepare']
    workflow_id: CatalogueID
    expected_revision: Revision
    workers: dict[CatalogueID, WorkerPreparation] = Field(min_length=1, max_length=31)
    idempotency_key: CommandKey


class OwnerApprove(OwnerBase):
    operation: Literal['approve_parent']
    workflow_id: CatalogueID
    expected_revision: Revision
    task_revisions: dict[CatalogueID, Revision] = Field(min_length=1, max_length=64)
    pairs: dict[CatalogueID, CatalogueID] = Field(min_length=2, max_length=31)
    integrator_id: CatalogueID
    profiles: dict[CatalogueID, ProfileRef] = Field(min_length=1, max_length=64)
    preparation_steps: int = Field(default=1, strict=True, ge=1, le=59)
    reruns: int = Field(default=0, strict=True, ge=0, le=2)
    retry_policies: dict[CatalogueID, RetryPolicy] = Field(default_factory=dict, max_length=31)
    idempotency_key: CommandKey


class OwnerReplan(OwnerBase):
    operation: Literal['replan']
    pipeline_id: CatalogueID
    expected_revision: Revision
    task_revisions: dict[CatalogueID, Revision] = Field(min_length=1, max_length=64)
    changes: dict[CatalogueID, TaskChange] = Field(default_factory=dict, max_length=64)
    new_branches: tuple[NewBranch, ...] = Field(default=(), max_length=30)
    profiles: dict[CatalogueID, ProfileRef] = Field(min_length=1, max_length=64)
    idempotency_key: CommandKey


class OwnerMessageRoutesPreview(OwnerBase):
    operation: Literal['message_routes_preview']
    workflow_id: CatalogueID


class OwnerMessageRoutes(OwnerBase):
    operation: Literal['message_routes']
    pipeline_id: CatalogueID
    expected_revision: Revision
    task_revisions: dict[CatalogueID, Revision] = Field(min_length=1, max_length=64)
    expected_grant_id: CatalogueID | None
    edges: tuple[MessageRouteEdge, ...] = Field(max_length=192)
    idempotency_key: CommandKey

    @model_validator(mode='after')
    def bounded_routes(self):
        pairs = [(edge.sender_task_id, edge.recipient_task_id) for edge in self.edges]
        if len(set(pairs)) != len(pairs):
            raise ValueError('Duplicate message route')
        for sender in {edge.sender_task_id for edge in self.edges}:
            if sum(edge.sender_task_id == sender for edge in self.edges) > 3:
                raise ValueError('At most three recipients per sender')
        return self


class OwnerIssueAgentContext(OwnerBase):
    operation: Literal['issue_agent_context']
    instance_id: CatalogueID
    expected_instance_revision: Revision
    idempotency_key: CommandKey


class AgentCredentialCommand(OwnerBase):
    instance_id: CatalogueID
    credential: str = Field(repr=False, pattern=r'^[0-9a-f]{32}\.[0-9a-f]{64}$')


class AgentRequestTask(AgentCredentialCommand):
    operation: Literal['request_task']
    request: AgentTaskRequest


class AgentRequestStatus(AgentCredentialCommand):
    operation: Literal['task_request_status']
    request_id: CatalogueID


OwnerCommand = Annotated[OwnerPreview | OwnerPrepare | OwnerApprove | OwnerReplan
    | OwnerMessageRoutes | OwnerMessageRoutesPreview | OwnerIssueAgentContext
    | AgentRequestTask | AgentRequestStatus, Field(discriminator='operation')]


def owner_request(value):
    return TypeAdapter(OwnerCommand).validate_python(value)


class OwnerTask(QueueModel):
    id: CatalogueID
    title: str
    kind: str
    revision: Revision
    state: str
    parent_id: CatalogueID | None
    dependencies: tuple[str, ...]
    profile_ids: tuple[str, ...]
    criteria_id: str | None
    criteria_version: int | None
    permission_approved: bool
    budget: BudgetAmount | None
    binding_id: CatalogueID | None
    blockers: tuple[str, ...]


class OwnerProfile(QueueModel):
    id: CatalogueID
    version: Revision
    title: str
    criteria_id: str | None
    criteria_version: int | None
    reruns: int
    available: bool
    reason: str | None = None


class OwnerResult(QueueModel):
    kind: Literal['preview', 'prepared', 'approved', 'replanned']
    project_id: CatalogueID
    project_revision: Revision
    workflow_id: CatalogueID
    workflow_revision: Revision
    pipeline_id: CatalogueID | None = None
    graph_revision: int = Field(default=0, strict=True, ge=0, le=2)
    binding_ids: dict[CatalogueID, CatalogueID] = Field(default_factory=dict, max_length=31)
    added_task_ids: tuple[CatalogueID, ...] = Field(default=(), max_length=64)
    tasks: tuple[OwnerTask, ...] = Field(default=(), max_length=64)
    profiles: tuple[OwnerProfile, ...] = Field(default=(), max_length=64)
    partial: bool = False


class OwnerMessageRoutesResult(QueueModel):
    kind: Literal['message_routes'] = 'message_routes'
    project_id: CatalogueID
    project_revision: Revision
    workflow_id: CatalogueID
    workflow_revision: Revision
    pipeline_id: CatalogueID | None
    grant_id: CatalogueID | None
    edges: tuple[MessageRouteEdge, ...] = Field(default=(), max_length=192)
    active: bool = False
    reason: str | None = None


class OwnerAgentContextResult(QueueModel):
    kind: Literal['agent_context'] = 'agent_context'
    issued: AgentRequestCredential


class OwnerTaskRequestResult(QueueModel):
    kind: Literal['task_request'] = 'task_request'
    receipt: AgentTaskRequestReceipt


class OwnerOutcome(QueueModel):
    result: OwnerResult | OwnerMessageRoutesResult | OwnerAgentContextResult | OwnerTaskRequestResult | None = None
    error_code: str | None = Field(default=None, pattern=r'^[a-z0-9_]+$', max_length=100)
