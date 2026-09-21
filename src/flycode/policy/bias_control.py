"""BIAS_CONTROL - the production default (plan §27-28).

final_score = llm_score + fly_bias, masked by safety, then ranked. The fly
never picks the action outright in this mode: it nudges the LLM's own
ranking, and safety has the only veto.
"""

from pydantic import BaseModel, Field

from ..domain import CandidateAction
from ..safety import SafetyDecision, apply_safety_mask


class RankedCandidate(BaseModel):
    candidate: CandidateAction
    llm_score: float
    fly_bias: float
    final_score: float
    allowed: bool


class BiasControlResult(BaseModel):
    ranked: list[RankedCandidate] = Field(default_factory=list)
    winner: CandidateAction | None = None
    safety: SafetyDecision


def _llm_score(candidate: CandidateAction) -> float:
    """Base score the LLM implicitly assigns via its own predictions."""
    return (
        0.5 * candidate.expected_progress
        + 0.3 * candidate.expected_information_gain
        + 0.2 * candidate.confidence
        - 0.3 * candidate.expected_risk
    )


def _bias_for(candidate: CandidateAction, fly_bias: dict[str, float]) -> float:
    """Look up bias by candidate id first (per-candidate scoring), else by tool
    name (the plan's illustrative examples key bias by tool/action name)."""
    if candidate.id in fly_bias:
        return fly_bias[candidate.id]
    return fly_bias.get(candidate.tool, 0.0)


def rank_candidates(
    candidates: list[CandidateAction],
    fly_bias: dict[str, float],
) -> BiasControlResult:
    """Combine LLM scores with fly bias, apply the safety mask, pick a winner (plan §28)."""
    safety = apply_safety_mask(candidates)
    allowed_by_id = {m.candidate.id: m.allowed for m in safety.masked}

    ranked: list[RankedCandidate] = []
    for masked in safety.masked:
        candidate = masked.candidate
        llm_score = _llm_score(candidate)
        bias = _bias_for(candidate, fly_bias)
        final = llm_score + bias
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                llm_score=llm_score,
                fly_bias=bias,
                final_score=final,
                allowed=allowed_by_id.get(candidate.id, False),
            )
        )

    ranked.sort(key=lambda r: r.final_score, reverse=True)
    winner = next((r.candidate for r in ranked if r.allowed), None)

    return BiasControlResult(ranked=ranked, winner=winner, safety=safety)
