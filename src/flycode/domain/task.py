"""Task definitions and behavior modes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BehaviorMode(StrEnum):
    """High-level behavioral states of the dynamic controller.

    These are NOT a plain FSM enum: the neural controller selects among them
    based on internal drives, valence, novelty and task progress.
    """

    EXPLORE = "explore"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    CONSOLIDATE = "consolidate"
    STOP = "stop"

    @property
    def is_terminal(self) -> bool:
        return self is BehaviorMode.STOP

    @property
    def advances_task(self) -> bool:
        """Modes that are expected to modify the repository."""
        return self in (BehaviorMode.EXECUTE, BehaviorMode.RECOVER)


# Soft prior over transitions (a bias, not a hard constraint).
VALID_TRANSITIONS: dict[BehaviorMode, tuple[BehaviorMode, ...]] = {
    BehaviorMode.EXPLORE: (BehaviorMode.DIAGNOSE, BehaviorMode.PLAN, BehaviorMode.EXECUTE),
    BehaviorMode.DIAGNOSE: (BehaviorMode.PLAN, BehaviorMode.EXECUTE, BehaviorMode.RECOVER),
    BehaviorMode.PLAN: (BehaviorMode.EXECUTE, BehaviorMode.VERIFY, BehaviorMode.EXPLORE),
    BehaviorMode.EXECUTE: (BehaviorMode.VERIFY, BehaviorMode.RECOVER, BehaviorMode.CONSOLIDATE),
    BehaviorMode.VERIFY: (BehaviorMode.RECOVER, BehaviorMode.CONSOLIDATE, BehaviorMode.EXECUTE),
    BehaviorMode.RECOVER: (BehaviorMode.EXECUTE, BehaviorMode.VERIFY, BehaviorMode.DIAGNOSE),
    BehaviorMode.CONSOLIDATE: (BehaviorMode.STOP, BehaviorMode.EXPLORE),
    BehaviorMode.STOP: (BehaviorMode.STOP,),
}


def is_valid_transition(current: BehaviorMode, nxt: BehaviorMode) -> bool:
    """Check whether a behavior-mode transition is allowed by the soft prior."""
    if nxt is current:
        return True
    return nxt in VALID_TRANSITIONS.get(current, ())


class TaskSpec(BaseModel):
    """Specification of a single software-engineering task."""

    model_config = ConfigDict(extra="allow")

    id: str
    task: str
    category: str = "generic"

    repo_path: str | None = None
    fixture: str | None = None

    expected_steps: int = Field(default=10, ge=1)
    max_steps: int = Field(default=25, ge=1)
    max_tool_calls: int = Field(default=60, ge=1)
    timeout_s: float = Field(default=300.0, gt=0)

    seed: int = 0

    success_criteria: dict = Field(default_factory=dict)
    broken_components: list[str] = Field(default_factory=list)
    expected_trajectory: list[BehaviorMode] = Field(default_factory=list)

    # Benchmark stressors (plan §64)
    hidden_observations: list[str] = Field(default_factory=list)
    delayed_reward_steps: int = Field(default=0, ge=0)
    reversal_after_steps: int | None = None
    stochastic_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    max_wall_time_s: float | None = None

    def is_resource_constrained(self) -> bool:
        return self.max_tool_calls <= 20

    def has_delayed_reward(self) -> bool:
        return self.delayed_reward_steps > 0

    def has_partial_observability(self) -> bool:
        return bool(self.hidden_observations)