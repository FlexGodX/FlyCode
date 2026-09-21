"""Observation model - compact view of the software environment state.

The full repository is never embedded in an observation (plan §7).
Only aggregated, truncated, decision-relevant signals are kept.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_LOGS = 40
MAX_ERRORS = 20
MAX_FILES = 200


class Observation(BaseModel):
    """Current state observation from SoftwareGym."""

    model_config = ConfigDict(extra="allow")

    task: str
    task_id: str = "task"
    step_number: int = Field(default=0, ge=0)

    # --- git ---------------------------------------------------------------
    git_state: dict[str, Any] = Field(default_factory=dict)
    # keys: branch, dirty, modified, untracked, staged, conflicts, last_commit

    # --- files -------------------------------------------------------------
    file_tree: list[str] = Field(default_factory=list, max_length=MAX_FILES)
    modified_files: list[str] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    created_files: list[str] = Field(default_factory=list)

    # --- build / tests -----------------------------------------------------
    build: dict[str, Any] = Field(default_factory=dict)
    # keys: status ("success"/"failed"/"unknown"), errors, warnings, returncode

    tests: dict[str, Any] = Field(default_factory=dict)
    # keys: status, passed, failed, errors, total, duration_s, failures (list[dict])

    # --- infrastructure ----------------------------------------------------
    docker: dict[str, Any] = Field(default_factory=dict)
    database: dict[str, Any] = Field(default_factory=dict)
    services: dict[str, Any] = Field(default_factory=dict)

    # --- logs --------------------------------------------------------------
    recent_logs: list[str] = Field(default_factory=list, max_length=MAX_LOGS)
    recent_errors: list[str] = Field(default_factory=list, max_length=MAX_ERRORS)

    # --- resources ---------------------------------------------------------
    resource_state: dict[str, Any] = Field(default_factory=dict)
    # keys: tool_calls, tool_call_limit, llm_calls, token_budget, wall_time_s

    # --- agent state -------------------------------------------------------
    action_history_summary: dict[str, Any] = Field(default_factory=dict)
    behavior_mode: str = "explore"
    neural_state: list[float] | None = None

    # ------------------------------------------------------------------ API
    def token_signature(self) -> str:
        """Stable short signature of decision-relevant state (for novelty)."""
        parts = [
            self.task_id,
            str(self.git_state.get("dirty", False)),
            str(self.tests.get("status", "unknown")),
            str(self.build.get("status", "unknown")),
            str(len(self.modified_files)),
            str(len(self.recent_errors)),
            self.behavior_mode,
        ]
        return "|".join(parts)

    @property
    def tests_status(self) -> str:
        return str(self.tests.get("status", "unknown"))

    @property
    def build_status(self) -> str:
        return str(self.build.get("status", "unknown"))

    @property
    def failing_tests(self) -> int:
        return int(self.tests.get("failed", 0) or 0) + int(self.tests.get("errors", 0) or 0)

    @property
    def tool_calls_used(self) -> int:
        return int(self.resource_state.get("tool_calls", 0) or 0)

    @property
    def is_healthy(self) -> bool:
        return (
            self.tests_status == "passed"
            and self.build_status in ("success", "unknown")
            and not self.recent_errors
        )

    def redacted(self, fields: list[str]) -> "Observation":
        """Return a copy with selected fields hidden (partial observability)."""
        if not fields:
            return self
        clone = self.model_copy(deep=True)
        for field in fields:
            if field == "tests":
                clone.tests = {"status": "hidden"}
            elif field == "build":
                clone.build = {"status": "hidden"}
            elif field == "recent_errors":
                clone.recent_errors = []
            elif field == "recent_logs":
                clone.recent_logs = []
            elif field == "git_state":
                clone.git_state = {}
            elif field == "docker":
                clone.docker = {}
            elif field == "database":
                clone.database = {}
            elif field == "services":
                clone.services = {}
            elif field == "file_tree":
                clone.file_tree = []
            elif hasattr(clone, field):
                setattr(clone, field, None)
        return clone