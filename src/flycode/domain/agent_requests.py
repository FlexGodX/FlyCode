"""Host-issued idle-instance identity, separate from retained runtime DTOs."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .orchestration import ClaimResult, QueueModel
from .verification_control import CatalogueID

Revision = Annotated[int, Field(strict=True, ge=1)]
CommandKey = Annotated[str, Field(min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f]+$')]
Fingerprint = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class AgentContextIssue(QueueModel):
    instance_id: CatalogueID
    expected_revision: Revision
    key: CommandKey


class AgentRequestContext(QueueModel):
    version: Literal[1] = 1
    id: CatalogueID
    project_id: CatalogueID
    project_revision: Revision
    project_hash: Fingerprint
    instance_id: CatalogueID
    instance_revision: Revision
    profile_id: CatalogueID
    profile_version: Revision
    profile_hash: Fingerprint
    host_registration_id: CatalogueID
    host_session_id: CatalogueID
    host_generation: Revision
    owner_id: str
    credential_hash: Fingerprint
    created_at: float
    expires_at: float


class AgentRequestCredential(QueueModel):
    context: AgentRequestContext
    credential: str = Field(repr=False, pattern=r'^[0-9a-f]{32}\.[0-9a-f]{64}$')


class AgentTaskRequest(QueueModel):
    expected_instance_revision: Revision
    idempotency_key: CommandKey
    capabilities: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] | None = Field(default=None, max_length=64)
    available: bool = Field(default=True, strict=True)
    preferred_task_id: CatalogueID | None = None
    expected_task_revision: Revision | None = None

    @model_validator(mode='after')
    def bounded_hints(self):
        if self.capabilities is not None and len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError('Capabilities must be unique')
        if (self.preferred_task_id is None) != (self.expected_task_revision is None):
            raise ValueError('A preferred task requires its revision')
        return self


class AgentTaskRequestReceipt(QueueModel):
    version: Literal[1] = 1
    id: CatalogueID
    context_id: CatalogueID
    project_id: CatalogueID
    instance_id: CatalogueID
    request: AgentTaskRequest
    state: Literal['queued', 'assigned', 'no_eligible_task', 'denied'] = 'queued'
    result: ClaimResult | None = None
    error_code: str | None = None
    created_at: float
    completed_at: float | None = None
    next_request_at: float | None = None
    seal: Fingerprint
