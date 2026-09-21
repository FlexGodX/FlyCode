"""Habituation - repeated, unhelpful actions lose attractiveness (plan §21).

Tracks per-fingerprint repetition and outcome history so the arbiter can
discount an action that keeps being chosen without moving the task forward,
independent of whatever the LLM's own rationale says about it.
"""

from pydantic import BaseModel, Field

from ..domain import ToolAction


class ActionFingerprint(BaseModel):
    """Coarse identity of an action, used to detect useless repetition (plan §21)."""

    mode: str
    tool: str
    target: str | None = None
    normalized_args: dict = Field(default_factory=dict)


class HabituationEntry(BaseModel):
    fingerprint: str
    count: int = 0
    successes: int = 0
    last_utility: float = 0.0


class HabituationModule:
    """Decays the bias for actions repeated without a useful outcome."""

    def __init__(self, decay: float = 0.25, window: int = 20) -> None:
        self.decay = decay
        self.window = window
        self.reset()

    def reset(self) -> None:
        self._entries: dict[str, HabituationEntry] = {}
        self._recent: list[str] = []

    def record(self, action: ToolAction, mode: str, utility: float, success: bool) -> HabituationEntry:
        key = action.fingerprint(mode)
        entry = self._entries.get(key) or HabituationEntry(fingerprint=key)
        entry.count += 1
        entry.successes += int(success)
        entry.last_utility = utility
        self._entries[key] = entry

        self._recent.append(key)
        if len(self._recent) > self.window:
            self._recent.pop(0)
        return entry

    def discount(self, action: ToolAction, mode: str) -> float:
        """Multiplicative discount in (0, 1] applied to this action's score."""
        key = action.fingerprint(mode)
        entry = self._entries.get(key)
        if entry is None or entry.count <= 1:
            return 1.0

        useless_repeats = entry.count - 1 - entry.successes
        if useless_repeats <= 0:
            return 1.0
        return max(0.1, 1.0 - self.decay * useless_repeats)

    def recent(self, n: int = 8) -> list[str]:
        return list(self._recent[-n:])

    def repetition_streak(self, action: ToolAction, mode: str) -> int:
        """Consecutive occurrences of this exact fingerprint at the tail."""
        key = action.fingerprint(mode)
        streak = 0
        for k in reversed(self._recent):
            if k != key:
                break
            streak += 1
        return streak

    @staticmethod
    def fingerprint_of(action: ToolAction, mode: str) -> ActionFingerprint:
        target = (
            action.arguments.get("path")
            or action.arguments.get("service")
            or action.arguments.get("url")
            or action.arguments.get("pattern")
        )
        return ActionFingerprint(mode=mode, tool=action.tool, target=target, normalized_args=dict(action.arguments))
