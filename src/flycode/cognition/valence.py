"""Dual valence - utility and aversion tracked separately (plan §18-19).

A single scalar `reward` hides conflicts (an action can be both useful and
dangerous). Keeping utility/aversion apart lets the arbiter see the conflict
instead of averaging it away, and lets the safety layer veto on aversion
alone regardless of utility.
"""

from pydantic import BaseModel, Field

from ..domain import Observation, ToolAction


class OutcomeSignals(BaseModel):
    """Dual-valence + novelty/surprise outcome signals for one transition."""

    utility: float = Field(default=0.0, ge=-1.0, le=1.0)
    aversion: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    surprise: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def conflict(self) -> float:
        """High when utility and aversion disagree strongly (plan §19)."""
        if self.utility <= 0:
            return 0.0
        return float(min(1.0, self.utility) * self.aversion)


class ValenceModule:
    """Estimates utility/aversion for an action given observation deltas.

    This is deliberately simple and hand-scored rather than learned: it is
    the ground-truth signal the mushroom body and RPE modules learn from.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last: OutcomeSignals = OutcomeSignals()

    def evaluate(
        self,
        action: ToolAction,
        previous: Observation,
        current: Observation,
    ) -> OutcomeSignals:
        utility = 0.0
        aversion = float(action.expected_risk)

        fixed = max(0, previous.failing_tests - current.failing_tests)
        broke = max(0, current.failing_tests - previous.failing_tests)
        utility += 0.25 * fixed
        utility -= 0.35 * broke

        if previous.build_status == "failed" and current.build_status == "success":
            utility += 0.3
        if previous.build_status == "success" and current.build_status == "failed":
            utility -= 0.3

        if previous.tests_status != "passed" and current.tests_status == "passed":
            utility += 0.4
        if previous.tests_status == "passed" and current.tests_status == "failed":
            utility -= 0.4
            aversion = max(aversion, 0.5)

        if len(current.recent_errors) > len(previous.recent_errors):
            aversion = max(aversion, 0.4)
        elif len(current.recent_errors) < len(previous.recent_errors):
            utility += 0.1

        if action.is_mutating and not (fixed or broke) and current.tests_status == previous.tests_status:
            utility -= 0.05  # mutation with no measurable effect: mild inefficiency penalty

        utility = max(-1.0, min(1.0, utility))
        aversion = max(0.0, min(1.0, aversion))

        signals = OutcomeSignals(utility=utility, aversion=aversion)
        self._last = signals
        return signals

    def get_state(self) -> OutcomeSignals:
        return self._last
