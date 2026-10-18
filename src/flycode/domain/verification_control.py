"""Server-owned immutable verification catalogue (plan §101, TST-13)."""
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from .source import SourceRoot
from .verification import (
    AcceptanceContract,
    CheckAttempt,
    CheckSpec,
    ProcessRef,
    SubjectSnapshot,
    TrustMode,
    VerificationModel,
    VerificationPlan,
    VerificationVerdict,
)

VerificationStage = Literal['baseline', 'target', 'regression', 'integration']


class VerificationProfileDraft(VerificationModel):
    title: str = Field(min_length=1, max_length=200)
    checks: list[CheckSpec] = Field(default_factory=lambda: [CheckSpec()], min_length=1, max_length=64)
    trust: TrustMode = 'trusted-local-process'
    reruns: int = Field(default=0, ge=0, le=2)
    acceptance: AcceptanceContract | None = None


class SubjectOrigin(VerificationModel):
    kind: Literal['workspace-baseline', 'artifact-baseline', 'artifact-target', 'integration-stage']
    record_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    record_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    checked_task_id: str | None = None
    source_subject_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_base: str | None = None
    stage: VerificationStage | None = None


class RegisteredSubject(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_revision: int = Field(ge=1)
    source_root: SourceRoot
    root: SourceRoot
    snapshot: SubjectSnapshot
    origin: SubjectOrigin | None = None
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float


class RegisteredVerificationProfile(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    version: Literal[1] = 1
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_revision: int = Field(ge=1)
    draft: VerificationProfileDraft
    approval_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float


CatalogueID = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
ExecutionState = Literal['queued', 'preparing', 'running', 'evaluating', 'completed', 'blocked',
    'cancelling', 'cancelled', 'interrupted', 'reconciling']


class VerificationBudget(VerificationModel):
    max_tool_calls: int = Field(ge=1, le=120)
    max_attempts: int = Field(ge=1, le=3)
    wall_seconds: float = Field(gt=0, le=1800.)


class VerificationPlanDraft(VerificationModel):
    subject_ids: dict[VerificationStage, CatalogueID] = Field(min_length=1, max_length=4)
    profile_id: CatalogueID
    profile_version: int = Field(ge=1)
    budget: VerificationBudget
    idempotency_key: str = Field(min_length=1, max_length=200)


class RegisteredVerificationPlan(VerificationModel):
    id: CatalogueID
    project_id: CatalogueID
    project_revision: int = Field(ge=1)
    workflow_id: str | None = None
    task_id: str | None = None
    task_contract_hash: str | None = None
    workflow_semantic_revision: int | None = None
    profile_id: CatalogueID
    profile_version: int = Field(ge=1)
    subject_ids: dict[VerificationStage, CatalogueID]
    budget: VerificationBudget
    plan: VerificationPlan
    plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    revision: int = Field(default=1, ge=1)
    state: Literal['draft', 'queued', 'preparing', 'running', 'evaluating', 'completed', 'blocked',
        'cancelling', 'cancelled', 'interrupted', 'reconciling'] = 'draft'
    current_execution_id: CatalogueID | None = None
    deadline_at: float | None = None
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float


class VerificationCommand(VerificationModel):
    operation: Literal['start', 'cancel', 'rerun']
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class VerificationInvocation(VerificationModel):
    id: CatalogueID = Field(default_factory=lambda: uuid4().hex)
    check_id: str
    number: int = Field(ge=1, le=3)
    fence: int = Field(ge=1)
    state: Literal['preparing', 'running', 'completed', 'uncertain'] = 'preparing'
    timeout: float = Field(gt=0, le=300)
    process: ProcessRef | None = None
    result: CheckAttempt | None = None
    late: bool = False


class VerificationExecution(VerificationModel):
    id: CatalogueID = Field(default_factory=lambda: uuid4().hex)
    plan_id: CatalogueID
    number: int = Field(ge=1, le=3)
    state: ExecutionState = 'queued'
    fence: int = Field(default=0, ge=0)
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float
    deadline_at: float
    ended_at: float | None = None
    started_at: float | None = None
    lease_until: float | None = None
    host_session_id: str | None = None
    host_generation: int | None = None
    coordinator_root: SourceRoot | None = None
    coordinator_identity: tuple[int, int] | None = None
    invocations: tuple[VerificationInvocation, ...] = Field(default=(), max_length=120)
    verdict: VerificationVerdict | None = None
    cancel_requested: bool = False
    current: bool = False
    error: str | None = None


class VerificationEvent(VerificationModel):
    sequence: int = Field(ge=1)
    project_id: CatalogueID
    plan_id: CatalogueID
    execution_id: CatalogueID | None = None
    actor_id: str
    kind: str
    occurred_at: float
    revision: int = Field(ge=1)
