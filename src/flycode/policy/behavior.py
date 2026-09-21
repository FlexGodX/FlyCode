"""Behavior-mode bookkeeping shared across all brain backends (plan §9).

Every backend proposes its own `BehaviorMode` in `NeuralState.behavior_mode`;
this module does not override that choice (the plan's transition table is a
soft prior, not a hard FSM) but tracks history and flags whether a proposed
transition matches the prior, which feeds the "strategy switches" metric
(plan §66).
"""

from pydantic import BaseModel, Field

from ..domain import BehaviorMode, is_valid_transition


class BehaviorHistoryEntry(BaseModel):
    mode: str
    expected: bool


class BehaviorArbiter:
    """Tracks the behavior-mode trajectory and flags unexpected jumps."""

    def __init__(self, initial: BehaviorMode = BehaviorMode.EXPLORE) -> None:
        self.current = initial
        self.history: list[BehaviorHistoryEntry] = []
        self.strategy_switches = 0

    def observe(self, proposed: str) -> BehaviorHistoryEntry:
        """Record a proposed mode and report whether it matched the soft prior."""
        proposed_mode = BehaviorMode(proposed)
        expected = is_valid_transition(self.current, proposed_mode)
        if proposed_mode != self.current:
            self.strategy_switches += 1
        entry = BehaviorHistoryEntry(mode=proposed_mode.value, expected=expected)
        self.history.append(entry)
        self.current = proposed_mode
        return entry

    def is_stagnating(self, window: int = 4) -> bool:
        """True if the last `window` entries are all the same non-verify mode."""
        if len(self.history) < window:
            return False
        recent = self.history[-window:]
        modes = {e.mode for e in recent}
        return len(modes) == 1 and self.current.advances_task
