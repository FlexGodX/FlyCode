"""Action models: concrete tool calls and LLM-generated candidates."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

VALID_TOOLS: tuple[str, ...] = (
    # MVP software tools (plan §49)
    "read_file",
    "list_files",
    "search",
    "git_status",
    "git_diff",
    "run_tests",
    "edit_file",
    "create_file",
    # Environment tools (plan §50)
    "docker_compose",
    "docker_logs",
    "postgres_status",
    "postgres_query",
    "alembic",
    "http_request",
    "service_health",
    # Control
    "stop",
)

READ_TOOLS: tuple[str, ...] = (
    "read_file",
    "list_files",
    "search",
    "git_status",
    "git_diff",
    "docker_logs",
    "postgres_status",
    "postgres_query",
    "service_health",
)

MUTATING_TOOLS: tuple[str, ...] = (
    "edit_file",
    "create_file",
    "docker_compose",
    "alembic",
)


class ToolAction(BaseModel):
    """A concrete tool invocation to be executed by the environment."""

    model_config = ConfigDict(extra="allow")

    id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    rationale: str = ""

    expected_progress: float = Field(default=0.5, ge=0.0, le=1.0)
    expected_information_gain: float = Field(default=0.3, ge=0.0, le=1.0)
    expected_risk: float = Field(default=0.1, ge=0.0, le=1.0)
    expected_cost: float = Field(default=0.1, ge=0.0, le=1.0)

    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    is_safe: bool = True
    category: str | None = None

    source: str = "llm"  # llm | fly | heuristic | human

    @property
    def is_read_only(self) -> bool:
        return self.tool in READ_TOOLS

    @property
    def is_mutating(self) -> bool:
        return self.tool in MUTATING_TOOLS

    @property
    def is_pinned_read(self) -> bool:
        artifact = self.arguments.get("artifact_id")
        return self.tool == "read_file" and isinstance(artifact, str) and bool(artifact)

    def fingerprint(self, mode: str = "execute") -> str:
        """Coarse identity of this action for habituation tracking."""
        target = (
            self.arguments.get("path")
            or self.arguments.get("service")
            or self.arguments.get("url")
            or self.arguments.get("pattern")
            or ""
        )
        key = self.arguments.get("key") or ""
        if self.is_pinned_read:
            return "pinned-read-v1:" + json.dumps(
                [mode, self.tool, target, key, self.arguments["artifact_id"]], separators=(",", ":")
            )
        return f"{mode}:{self.tool}:{target}:{key}"


class CandidateAction(ToolAction):
    """An LLM-proposed candidate enriched with predictions (plan §25)."""

    rank_score: float | None = None
    fly_bias: float = 0.0
    fly_rank_score: float | None = None
    safety_reason: str | None = None


DEFAULT_CANDIDATE_COUNT = 4
