"""Canonical queue contracts shared by dispatcher and tracker (Plan §§100,103)."""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .action import MUTATING_TOOLS, VALID_TOOLS
from .verification import AcceptanceContract

TaskState = Literal['planned', 'ready', 'assigned', 'running', 'awaiting_review', 'succeeded',
                    'blocked', 'retry_wait', 'failed', 'cancelled']
AttemptState = Literal['assigned', 'running', 'stopping', 'reconciling', 'awaiting_review',
                       'succeeded', 'failed', 'cancelled', 'interrupted']


class QueueModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class AccessScope(QueueModel):
    """Issued by the local host; never accepted as authority from task text/JSON."""

    actor_id: str = Field(min_length=1, max_length=200)
    project_ids: frozenset[str]
    role: Literal['reader', 'owner', 'runtime'] = 'reader'
    global_queue_control: bool = False


class BudgetAmount(QueueModel):
    model_calls: int = Field(default=0, ge=0, le=60)
    tool_calls: int = Field(default=0, ge=0, le=120)


class WorkflowBudget(QueueModel):
    max_model_calls: int = Field(default=60, ge=0, le=60)
    max_tool_calls: int = Field(default=120, ge=0, le=120)
    max_instances: int = Field(default=4, ge=1, le=4)
    max_tasks: int = Field(default=64, ge=1, le=64)
    max_attempts: int = Field(default=3, ge=1, le=3)
    max_depth: int = Field(default=3, ge=0, le=3)
    max_children: int = Field(default=8, ge=1, le=8)
    wall_seconds: float = Field(default=1800., gt=0, le=1800)
    verification_fraction: float = Field(default=.2, ge=.2, le=1.)


class BudgetLedger(QueueModel):
    limit: BudgetAmount
    spent: BudgetAmount
    reserved: BudgetAmount
    free: BudgetAmount
    uncertain: bool = False


class ExecutionPermission(QueueModel):
    approved: bool = False
    tools: tuple[str, ...] = ()
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()

    @field_validator('tools')
    @classmethod
    def known_tools(cls, values):
        if len(set(values)) != len(values) or any(value not in VALID_TOOLS for value in values):
            raise ValueError('Unknown or duplicate tool permission')
        return values

    @field_validator('read_scopes', 'write_scopes')
    @classmethod
    def relative_scopes(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if (not value or path.is_absolute() or '..' in path.parts or '\\' in value
                    or str(path) != value or any(ord(char) < 32 for char in value)
                    or any(part in {'.git', '.ssh', '.aws', '.venv'} or part.startswith('.env') for part in path.parts)):
                raise ValueError('File scopes must be normalized project-relative paths')
        if len(set(values)) != len(values):
            raise ValueError('Duplicate file scope')
        return values


class TaskDraft(QueueModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(default='', max_length=20000)
    kind: Literal['implementation', 'research', 'review'] = 'implementation'
    acceptance: AcceptanceContract | None = None
    permissions: ExecutionPermission | None = None
    budget: BudgetAmount | None = None
    parent_id: str | None = None
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    allowed_profile_ids: tuple[str, ...] = ()
    input_artifacts: dict[str, str] = Field(default_factory=dict)
    base_snapshot_hash: str | None = None
    priority: int = Field(default=1, ge=0, le=3)
    due_at: float | None = Field(default=None, ge=0)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    critical_path_fraction: float = Field(default=0., ge=0, le=1)
    expected_progress: float = Field(default=.5, ge=0, le=1)
    risk: float = Field(default=0., ge=0, le=1)

    @field_validator('title')
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError('Title cannot be blank')
        return value

    @field_validator('tags', 'dependencies', 'capabilities', 'allowed_profile_ids')
    @classmethod
    def bounded_unique_strings(cls, values):
        if len(set(values)) != len(values) or any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError('Expected unique nonempty values up to 200 characters')
        return values

    @model_validator(mode='after')
    def tag_limits(self):
        if any(len(tag) > 32 for tag in self.tags):
            raise ValueError('Tags are limited to 32 characters')
        return self


class WorkItem(TaskDraft):
    id: str
    short_id: str
    project_id: str
    workflow_id: str
    revision: int = Field(default=1, ge=1)
    semantic_revision: int = Field(default=1, ge=1)
    state: TaskState = 'planned'
    paused: bool = False
    archived: bool = False
    current_attempt_id: str | None = None
    fence: int = Field(default=0, ge=0)
    created_at: float
    updated_at: float
    queued_at: float | None = None

    @property
    def contract_hash(self) -> str:
        fields = ('objective', 'kind', 'acceptance', 'permissions', 'budget', 'parent_id', 'dependencies',
                  'capabilities', 'allowed_profile_ids', 'input_artifacts', 'base_snapshot_hash')
        data = self.model_dump(mode='json', include=set(fields))
        if self.acceptance is not None:
            data['acceptance'] = self.acceptance.model_dump(mode='json', exclude={'title'})
            data['acceptance']['criteria'] = [item.model_dump(exclude={'title'}) for item in self.acceptance.criteria]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class WorkflowDraft(QueueModel):
    project_id: str = Field(min_length=1)
    task: TaskDraft
    budget: WorkflowBudget | None = None


class WorkflowSpec(QueueModel):
    id: str
    project_id: str
    root_task_id: str
    revision: int = Field(default=1, ge=1)
    semantic_revision: int = Field(default=1, ge=1)
    budget: WorkflowBudget | None = None
    state: Literal['draft', 'running', 'pausing', 'paused', 'cancelling', 'succeeded',
                   'incomplete', 'failed', 'cancelled'] = 'draft'
    active_slot: bool = False
    created_at: float
    started_at: float | None = None
    deadline_at: float | None = None
    dispatch_count: int = Field(default=0, ge=0)


class AgentProfileDraft(QueueModel):
    role: Literal['planner', 'researcher', 'developer', 'reviewer', 'decider']
    model_ref: str = Field(min_length=1)
    capability_version: str = 'structured-candidates+option-logprobs-v1'
    instructions: str = Field(min_length=1, max_length=20000)
    capabilities: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = Field(min_length=1)
    permissions: ExecutionPermission
    context_limit: int = Field(gt=0, le=131072)

    @model_validator(mode='after')
    def role_permissions(self):
        if self.role != 'developer' and (self.permissions.write_scopes or set(self.permissions.tools) & set(MUTATING_TOOLS)):
            raise ValueError('Only developers may have project write scopes')
        if self.role == 'decider' and self.permissions.tools:
            raise ValueError('Decision profiles have no tools')
        return self


class AgentProfile(AgentProfileDraft):
    id: str
    version: int = 1
    instruction_hash: str
    model_id: str | None = None
    endpoint_ref: str | None = None
    model_revision: int | None = None
    available: bool = False


class AgentInstance(QueueModel):
    id: str
    profile_id: str
    profile_version: int
    revision: int = 1
    state: Literal['idle', 'assigned', 'running', 'reconciling'] = 'idle'
    current_attempt_id: str | None = None


class AgentAttempt(QueueModel):
    id: str
    workflow_id: str
    workflow_revision: int = Field(ge=1)
    workflow_semantic_revision: int = Field(ge=1)
    task_id: str
    task_revision: int
    semantic_revision: int
    contract_hash: str
    profile_id: str
    profile_version: int
    profile_snapshot: AgentProfile
    instance_id: str
    fence: int
    state: AttemptState = 'assigned'
    initial_budget: BudgetAmount
    reserved: BudgetAmount
    spent: BudgetAmount = Field(default_factory=BudgetAmount)
    verification: bool = False
    created_at: float
    started_at: float | None = None
    lease_until: float
    stop_confirmed: bool = False


class TaskCommand(QueueModel):
    operation: Literal['enqueue', 'pause', 'resume', 'cancel', 'set_priority', 'set_metadata',
                       'assign_profile', 'release_assignment', 'retry', 'revise', 'archive', 'reopen']
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    changes: dict[str, Any] = Field(default_factory=dict)


class ClaimRequest(QueueModel):
    instance_id: str
    expected_instance_revision: int = Field(ge=1)
    task_id: str | None = None
    expected_task_revision: int | None = Field(default=None, ge=1)
    reserve: BudgetAmount
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def explicit_reservation(self):
        if self.reserve.model_calls + self.reserve.tool_calls == 0:
            raise ValueError('Assignment requires a nonempty reservation')
        if (self.task_id is None) != (self.expected_task_revision is None):
            raise ValueError('Preferred task requires its revision')
        return self


class ClaimResult(QueueModel):
    attempt: AgentAttempt | None = None
    reasons: tuple[str, ...] = ()
    retry_after_s: int = 30


class QueueEvent(QueueModel):
    sequence: int
    project_id: str
    workflow_id: str
    actor_id: str
    kind: str
    occurred_at: float
    task_id: str | None = None
    attempt_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ModelUsage(QueueModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ModelCall(QueueModel):
    id: str
    workflow_id: str
    task_id: str
    attempt_id: str
    instance_id: str
    fence: int = Field(ge=1)
    endpoint_ref: str
    model_id: str
    role: Literal['primary', 'scorer']
    status: Literal['running', 'completed', 'failed', 'uncertain'] = 'running'
    started_at: float
    ended_at: float | None = None
    error_class: str | None = Field(default=None, max_length=200)
    usage: ModelUsage | None = None
    late: bool = False


class ModelAdmission(QueueModel):
    call: ModelCall
    fresh: bool
