"""Host-derived immutable results and exact-byte review bindings (Plan §104)."""

from typing import Literal

from pydantic import Field, field_validator

from .orchestration import CompletionInput, ExecutionPermission, QueueModel
from .source import SourceRoot
from .verification import SubjectSnapshot, VerificationVerdict


class FileVersion(QueueModel):
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    mode: int = Field(ge=0, le=0o7777)


class FileChange(QueueModel):
    path: str
    before: FileVersion | None
    after: FileVersion | None

    @field_validator('path')
    @classmethod
    def relative_path(cls, value):
        ExecutionPermission(read_scopes=(value,))
        ExecutionPermission(read_scopes=(value.casefold(),))
        if value == '.':
            raise ValueError('A changed file needs a relative filename')
        return value


class ApplicationOperation(QueueModel):
    path: str
    identity: tuple[int, int, int, int, int] | None = None
    before_identity: tuple[int, int, int, int, int] | None = None


class ApplicationJournal(QueueModel):
    version: Literal['application-journal-v1'] = 'application-journal-v1'
    state: Literal['applying', 'applied', 'rolled_back', 'reconciling']
    changes: tuple[FileChange, ...]
    operations: tuple[ApplicationOperation, ...] = ()


class PatchArtifact(QueueModel):
    id: str
    project_id: str
    workflow_id: str
    task_id: str
    attempt_id: str
    fence: int
    semantic_revision: int
    contract_hash: str
    workspace_id: str
    result_hash: str
    base: SubjectSnapshot
    baseline: SubjectSnapshot
    target: SubjectSnapshot
    changes: tuple[FileChange, ...]
    patch_hash: str
    created_at: float
    prerequisites: dict[str, CompletionInput] = Field(default_factory=dict)


class ReviewRecord(QueueModel):
    id: str
    artifact_id: str
    patch_hash: str
    reviewer_attempt_id: str
    reviewer_fence: int
    reviewer_contract_hash: str
    approval_hash: str
    reruns: int = Field(default=0, ge=0, le=2)
    status: Literal['approved', 'changes_requested', 'inconclusive']
    verdict: VerificationVerdict
    created_at: float


class TaskCompletion(QueueModel):
    version: Literal[1] = 1
    id: str
    project_id: str
    workflow_id: str
    task_id: str
    semantic_revision: int
    contract_hash: str
    attempt_id: str
    fence: int
    artifact_id: str
    artifact_hash: str
    review_id: str
    review_hash: str
    reviewer_task_id: str
    reviewer_semantic_revision: int
    reviewer_contract_hash: str
    prerequisites: dict[str, CompletionInput]
    runtime_root: SourceRoot
    source_root: SourceRoot
    source_hash: str
    created_at: float


class ReviewExecution(QueueModel):
    attempt_id: str
    project_id: str
    workflow_id: str
    task_id: str
    fence: int
    artifact_id: str
    patch_hash: str
    approval_hash: str
    reruns: int = Field(ge=0, le=2)
    runtime_root: str
    coordinator_identity: tuple[int, int]
    state: Literal['running', 'completed', 'reconciling', 'interrupted'] = 'running'
    review_id: str | None = None
    error: str | None = None
    created_at: float


class IntegrationRecord(QueueModel):
    id: str
    project_id: str
    workflow_id: str
    attempt_id: str
    fence: int
    source_root: str
    runtime_root: str | None = None
    previous_result_id: str | None = None
    coordinator_identity: tuple[int, int] | None = None
    source_claim_id: str | None = None
    source_before: SubjectSnapshot
    artifact_hashes: dict[str, str]
    review_ids: tuple[str, ...]
    contracts: dict[str, str]
    semantic_revisions: dict[str, int] = Field(default_factory=dict)
    approval_hashes: dict[str, str] = Field(default_factory=dict)
    reruns: int = Field(default=0, ge=0, le=2)
    author_attempts: dict[str, str]
    author_completions: dict[str, CompletionInput] = Field(default_factory=dict)
    changes: tuple[FileChange, ...] = ()
    candidate: SubjectSnapshot | None = None
    state: Literal['preparing', 'verifying', 'applying', 'reconciling', 'completed', 'failed', 'conflict', 'invalidated'] = 'preparing'
    verdicts: dict[str, VerificationVerdict] = Field(default_factory=dict)
    applied_verdicts: dict[str, VerificationVerdict] = Field(default_factory=dict)
    conflict_paths: tuple[str, ...] = ()
    resolution_task_id: str | None = None
    resolution_blocker: str | None = None
    resolution_attempts: tuple[str, ...] = ()
    resolved_conflicts: tuple[str, ...] = ()
    error: str | None = None
    created_at: float
