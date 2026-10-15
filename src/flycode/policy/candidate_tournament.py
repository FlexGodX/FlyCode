"""Candidate Tournament - selects a control mode and returns the winner (plan §26-27).

A thin dispatcher over `bias_control` / `hard_control` so callers (the
orchestrator, the benchmark runner) pick HARD_CONTROL vs BIAS_CONTROL with a
single enum instead of importing both modules directly.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain import CandidateAction
from . import bias_control, hard_control


class ControlMode(StrEnum):
    HARD_CONTROL = "hard_control"
    BIAS_CONTROL = "bias_control"


class TournamentResult(BaseModel):
    mode: ControlMode
    winner: CandidateAction | None
    blocked: list[str] = Field(default_factory=list)
    #: Final scores per candidate id (allowed candidates only), for telemetry.
    scores: dict[str, float] = Field(default_factory=dict)


def run_tournament(
    candidates: list[CandidateAction],
    fly_bias: dict[str, float],
    mode: ControlMode = ControlMode.BIAS_CONTROL,
) -> TournamentResult:
    if not candidates:
        return TournamentResult(mode=mode, winner=None, blocked=[])

    if mode is ControlMode.HARD_CONTROL:
        result = hard_control.select_candidate(candidates, fly_bias)
        blocked = [m.candidate.id for m in result.safety.blocked_candidates]
        allowed_ids = {m.candidate.id for m in result.safety.masked if m.allowed}
        scores = {cid: s for cid, s in result.scored.items() if cid in allowed_ids}
        return TournamentResult(mode=mode, winner=result.winner, blocked=blocked, scores=scores)

    result = bias_control.rank_candidates(candidates, fly_bias)
    blocked = [m.candidate.id for m in result.safety.blocked_candidates]
    scores = {r.candidate.id: r.final_score for r in result.ranked if r.allowed}
    return TournamentResult(mode=mode, winner=result.winner, blocked=blocked, scores=scores)
