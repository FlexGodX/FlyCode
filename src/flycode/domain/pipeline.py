"""Owner-approved future-output recipes; concrete stages remain immutable."""
from typing import Literal

from pydantic import Field

from .orchestration import AgentProfile, QueueModel, WorkflowBudget, WorkItem


class PipelineRecipe(QueueModel):
    version: int = Field(default=1, ge=1, le=1)
    id: str
    owner_id: str
    project_id: str
    workflow_id: str
    workflow_semantic_revision: int
    workflow_budget: WorkflowBudget
    writer_id: str
    reviewer_id: str
    integrator_id: str
    tasks: dict[str, WorkItem]
    profiles: dict[str, AgentProfile]
    worker_binding_id: str
    worker_binding_hash: str
    source_root: str
    runtime_root: str
    source_hash: str
    approval_hashes: dict[str, str]
    reruns: int = Field(default=0, ge=0, le=2)
    approval_sequence: int
    created_at: float


class PipelineBranch(QueueModel):
    writer_id: str
    reviewer_id: str
    worker_binding_id: str
    worker_binding_hash: str


class _MultiPipelineRecipe(QueueModel):
    version: int
    id: str
    owner_id: str
    project_id: str
    workflow_id: str
    workflow_semantic_revision: int
    workflow_budget: WorkflowBudget
    branches: tuple[PipelineBranch, ...] = Field(min_length=1, max_length=3)
    integrator_id: str
    tasks: dict[str, WorkItem]
    profiles: dict[str, AgentProfile]
    source_root: str
    runtime_root: str
    source_hash: str
    approval_hashes: dict[str, str]
    reruns: int = Field(default=0, ge=0, le=2)
    approval_sequence: int
    created_at: float


class TeamPipelineRecipe(_MultiPipelineRecipe):
    version: Literal[2] = 2


class DependencyPipelineRecipe(_MultiPipelineRecipe):
    version: Literal[3] = 3
    branches: tuple[PipelineBranch, ...] = Field(min_length=1, max_length=31)


class ParentPipelineRecipe(_MultiPipelineRecipe):
    version: Literal[4] = 4
    branches: tuple[PipelineBranch, ...] = Field(min_length=2, max_length=31)
    parent_id: str
    preparation_steps: int = Field(ge=1, le=59)


class ContinuationCheckpoint(QueueModel):
    id: str
    pipeline_id: str
    parent_id: str
    artifact_id: str
    artifact_hash: str
    attempt_id: str
    fence: int
    semantic_revision: int
    contract_hash: str
    created_at: float
