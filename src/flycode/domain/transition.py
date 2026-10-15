"""Transition model - outcome of executing one tool action."""

from pydantic import BaseModel, ConfigDict, Field

from .action import ToolAction
from .observation import Observation


class ObservationDelta(BaseModel):
    """Structured difference between two observations."""

    tests_status_changed: bool = False
    build_status_changed: bool = False
    new_failing_tests: int = 0
    fixed_tests: int = 0
    files_touched: list[str] = Field(default_factory=list)
    new_errors: list[str] = Field(default_factory=list)
    resolved_errors: list[str] = Field(default_factory=list)


class Transition(BaseModel):
    """Environment feedback after an action (plan §8)."""

    model_config = ConfigDict(extra="allow")

    previous: Observation
    action: ToolAction
    current: Observation

    # Dual-valence outcome signals (plan §18)
    utility: float = Field(default=0.0, ge=-1.0, le=1.0)
    aversion: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    surprise: float = Field(default=0.0, ge=0.0, le=1.0)

    information_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    cost: float = Field(default=0.0, ge=0.0, le=1.0)

    success: bool = False
    error_message: str = ""
    stdout: str = ""
    stderr: str = ""
    payload: dict = Field(default_factory=dict)

    delta: ObservationDelta = Field(default_factory=ObservationDelta)

    neural_state_delta: list[float] | None = None

    previous_mode: str = "execute"
    next_mode: str | None = None

    elapsed_s: float = 0.0
    tool_call_index: int = 0

    @property
    def reward(self) -> float:
        """Scalar reward used for RPE / value learning."""
        base = self.utility - 0.7 * self.aversion + 0.3 * self.information_gain
        if self.success:
            base += 0.2
        return max(-1.0, min(1.0, base))

    @property
    def is_regression(self) -> bool:
        """Did this action make things measurably worse?

        Only fires against a previously *measured* test state: when the
        previous observation has no test result yet (status "unknown"), a
        failing first `run_tests` discovers a pre-existing breakage rather
        than causing one, so it is not a regression.
        """
        prev_status = self.previous.tests_status
        if prev_status not in ("passed", "failed"):
            return False
        return self.delta.new_failing_tests > 0 or (
            self.delta.tests_status_changed and self.current.tests_status == "failed"
            and prev_status == "passed"
        )