"""Canonical queue contracts shared by dispatcher and tracker (Plan §§100,103)."""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .action import MUTATING_TOOLS, VALID_TOOLS
from .task import TaskSpec
from .verification import AcceptanceContract, ProcessRef, SubjectSnapshot

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
        if len(set(values)) != len(values) or any(value not in (*VALID_TOOLS, 'send_message') for value in values):
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
    result_id: str | None = None
    blocked_reason: str | None = None
    retired_by_result: str | None = None

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


class WorkerBinding(QueueModel):
    id: str
    task_id: str
    workflow_id: str
    project_id: str
    owner_id: str
    revision: int = Field(ge=1)
    previous_id: str | None = None
    semantic_revision: int
    workflow_semantic_revision: int
    contract_hash: str
    source_root: str
    runtime_root: str
    source_hash: str = Field(pattern='^[0-9a-f]{64}$')
    brain: str
    seed: int = Field(ge=0, le=2**32 - 1)
    created_at: float

    def matches(self, task: WorkItem, workflow: WorkflowSpec) -> bool:
        return (self.task_id == task.id and self.workflow_id == workflow.id and self.project_id == task.project_id
            and self.semantic_revision == task.semantic_revision and self.contract_hash == task.contract_hash
            and self.workflow_semantic_revision == workflow.semantic_revision and task.kind != 'review')


class ReviewBinding(QueueModel):
    mode: Literal['review'] = 'review'
    id: str
    task_id: str
    workflow_id: str
    project_id: str
    owner_id: str
    revision: int = Field(ge=1)
    previous_id: str | None = None
    semantic_revision: int
    workflow_semantic_revision: int
    contract_hash: str
    runtime_root: str
    artifact_id: str
    patch_hash: str
    artifact_hash: str
    author_attempt_id: str
    author_fence: int
    author_instance_id: str
    author_semantic_revision: int
    author_contract_hash: str
    approval_hash: str = Field(pattern='^[0-9a-f]{64}$')
    reruns: int = Field(default=0, ge=0, le=2)
    created_at: float

    def matches(self, task: WorkItem, workflow: WorkflowSpec) -> bool:
        return (self.task_id == task.id and self.workflow_id == workflow.id and self.project_id == task.project_id
            and self.semantic_revision == task.semantic_revision and self.contract_hash == task.contract_hash
            and self.workflow_semantic_revision == workflow.semantic_revision and task.kind == 'review'
            and task.input_artifacts == {self.artifact_id: self.patch_hash})


class IntegrationBinding(QueueModel):
    mode: Literal['integration'] = 'integration'
    id: str
    task_id: str
    workflow_id: str
    project_id: str
    owner_id: str
    revision: int = Field(ge=1)
    previous_id: str | None = None
    semantic_revision: int
    workflow_semantic_revision: int
    contract_hash: str
    runtime_root: str
    source_root: str
    source_hash: str = Field(pattern='^[0-9a-f]{64}$')
    artifact_hashes: dict[str, str]
    artifact_records: dict[str, str]
    review_ids: tuple[str, ...]
    review_records: dict[str, str]
    author_attempts: dict[str, str]
    contracts: dict[str, str]
    semantic_revisions: dict[str, int]
    approval_hashes: dict[str, str]
    previous_result_id: str | None = None
    resolved_conflicts: tuple[str, ...] = ()
    reruns: int = Field(default=0, ge=0, le=2)
    created_at: float

    def matches(self, task: WorkItem, workflow: WorkflowSpec) -> bool:
        return (self.task_id == task.id and self.workflow_id == workflow.id and self.project_id == task.project_id
            and self.semantic_revision == task.semantic_revision and self.contract_hash == task.contract_hash
            and self.workflow_semantic_revision == workflow.semantic_revision and task.kind == 'review'
            and task.input_artifacts == self.artifact_hashes)


def stage_binding(raw: str) -> ReviewBinding | IntegrationBinding:
    data = json.loads(raw)
    return (IntegrationBinding if data.get('mode') == 'integration' else ReviewBinding).model_validate(data)


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
    loaded_instance_id: str | None = None
    context_capacity: int | None = Field(default=None, gt=0)
    load_fingerprint: str | None = None
    unavailable_reason: str | None = None
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
    execution_binding_id: str | None = None
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


class AutoClaimRequest(QueueModel):
    instance_id: str
    expected_instance_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


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


class HostModelCall(QueueModel):
    id: str
    owner_id: str = Field(min_length=1, max_length=200)
    owner_kind: Literal['legacy', 'probe']
    endpoint_ref: str
    model_id: str
    role: Literal['primary', 'scorer']
    status: Literal['running', 'completed', 'failed', 'uncertain'] = 'running'
    started_at: float
    ended_at: float | None = None
    error_class: str | None = Field(default=None, max_length=200)
    usage: ModelUsage | None = None
    late: bool = False


class ToolCall(QueueModel):
    id: str
    workflow_id: str
    task_id: str
    attempt_id: str
    instance_id: str
    fence: int = Field(ge=1)
    tool: str
    argument_hash: str
    status: Literal['running', 'completed', 'failed', 'uncertain'] = 'running'
    started_at: float
    ended_at: float | None = None
    error_class: str | None = Field(default=None, max_length=200)
    late: bool = False
    execution_profile: Literal['registered-pytest-v1', 'artifact-read-v1'] | None = None
    process: ProcessRef | None = None


class WorkspaceRecord(QueueModel):
    id: str
    attempt_id: str
    fence: int = Field(ge=1)
    contract_hash: str
    source_root: str
    snapshot: SubjectSnapshot
    checkout_revision: str
    created_at: float


class WorkerRecord(QueueModel):
    id: str
    process: ProcessRef | None = None
    attempt_id: str
    fence: int = Field(ge=1)
    state: Literal['starting', 'running', 'exited', 'uncertain'] = 'starting'
    pid: int | None = Field(default=None, gt=0)
    created_at: float
    stopped_at: float | None = None
    stop_confirmed: bool = False
    exit_code: int | None = None
    result_hash: str | None = None
    error: str | None = None


class WorkerConfig(QueueModel):
    database: str
    repository: str
    scope: AccessScope
    attempt_id: str
    fence: int
    task: TaskSpec
    brain: str
    seed: int
    message_recipients: tuple[str, ...] = ()


class WorkerRequest(QueueModel):
    sequence: int = Field(ge=1)
    operation: Literal['ready', 'candidates', 'activity', 'message', 'message_tool', 'artifact_tool', 'result', 'error']
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerReply(QueueModel):
    sequence: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class MessageTaskRequest(QueueModel):
    kind: Literal['task_request']
    objective: str = Field(min_length=1, max_length=12000)


class MessageQuestion(QueueModel):
    kind: Literal['question']
    question: str = Field(min_length=1, max_length=12000)


class MessageAnswer(QueueModel):
    kind: Literal['answer']
    answer: str = Field(min_length=1, max_length=12000)


class MessageProgress(QueueModel):
    kind: Literal['progress']
    summary: str = Field(min_length=1, max_length=12000)
    step: int | None = Field(default=None, ge=0, le=121)


class MessageArtifactReady(QueueModel):
    kind: Literal['artifact_ready']
    summary: str = Field(min_length=1, max_length=12000)


class MessageReviewResult(QueueModel):
    kind: Literal['review_result']
    recommendation: Literal['approve', 'changes_requested', 'inconclusive']
    summary: str = Field(min_length=1, max_length=12000)


class MessageHandoff(QueueModel):
    kind: Literal['handoff']
    summary: str = Field(min_length=1, max_length=12000)


class MessageCancel(QueueModel):
    kind: Literal['cancel']
    reason: str = Field(min_length=1, max_length=12000)


MessageContent = Annotated[MessageTaskRequest | MessageQuestion | MessageAnswer | MessageProgress
    | MessageArtifactReady | MessageReviewResult | MessageHandoff | MessageCancel, Field(discriminator='kind')]


class MessageArtifact(QueueModel):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class MessageDraft(QueueModel):
    recipients: tuple[str, ...] = Field(min_length=1, max_length=3)
    content: MessageContent
    idempotency_key: str = Field(min_length=1, max_length=200)
    causation_id: str | None = Field(default=None, min_length=1, max_length=200)
    ttl_seconds: float = Field(default=300., gt=0, le=300.)
    artifacts: tuple[MessageArtifact, ...] = Field(default=(), max_length=8)

    @field_validator('recipients')
    @classmethod
    def explicit_recipients(cls, values):
        if len(set(values)) != len(values) or any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError('Expected unique explicit recipients')
        return values

    @model_validator(mode='after')
    def artifact_content(self):
        if self.content.kind == 'artifact_ready' and not self.artifacts:
            raise ValueError('Artifact-ready messages require approved artifact references')
        if len({item.id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError('Duplicate artifact reference')
        return self


class AgentMessage(QueueModel):
    id: str
    batch_id: str
    schema_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    project_id: str
    workflow_id: str
    task_id: str
    attempt_id: str
    sender_id: str
    sender_fence: int = Field(ge=1)
    recipient_id: str
    recipient_attempt_id: str
    recipient_fence: int = Field(ge=1)
    correlation_id: str
    causation_id: str | None
    hops: int = Field(ge=0, le=4)
    created_at: float
    expires_at: float
    idempotency_key: str
    content: MessageContent
    artifacts: tuple[MessageArtifact, ...] = ()


class MessageReceipt(QueueModel):
    message_id: str
    recipient_attempt_id: str
    received_at: float
    outcome: Literal['received', 'audit']
    reason: str | None = None
