"""HARD_CONTROL - the fly decides the final candidate outright (plan §27).

Research/benchmark mode only: safety still has the only veto, but the fly's
own per-candidate score (not a blend with the LLM's) picks the winner among
whatever survives the mask.
"""

from pydantic import BaseModel, Field

from ..domain import CandidateAction
from ..safety import SafetyDecision, apply_safety_mask


class HardControlResult(BaseModel):
    scored: dict[str, float] = Field(default_factory=dict)
    winner: CandidateAction | None = None
    safety: SafetyDecision


def select_candidate(
    candidates: list[CandidateAction],
    fly_scores: dict[str, float],
) -> HardControlResult:
    """The fly's own scores alone decide, after the safety mask (plan §27)."""
    safety = apply_safety_mask(candidates)
    allowed = {m.candidate.id: m.candidate for m in safety.masked if m.allowed}

    if not allowed:
        return HardControlResult(scored=fly_scores, winner=None, safety=safety)

    winner_id = max(allowed, key=lambda cid: fly_scores.get(cid, 0.0))
    return HardControlResult(scored=fly_scores, winner=allowed[winner_id], safety=safety)
