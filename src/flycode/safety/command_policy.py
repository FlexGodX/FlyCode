"""Safety mask - the final gate between ranked candidates and execution (plan §29).

Pipeline: candidate scores -> fly bias -> risk detector -> safety mask -> final
selection. The fly-core (or the LLM) can rank candidates however it likes;
this module has the only authority to veto or force a mode switch, and it
never delegates that authority downstream.
"""

from pydantic import BaseModel, Field

from ..domain import BehaviorMode, CandidateAction
from .risk import RiskAssessment, assess_risk, requires_verification


class MaskedCandidate(BaseModel):
    """A candidate after the safety mask has been applied."""

    candidate: CandidateAction
    assessment: RiskAssessment
    allowed: bool


class SafetyDecision(BaseModel):
    """Result of masking a full candidate set."""

    masked: list[MaskedCandidate] = Field(default_factory=list)
    forced_mode: BehaviorMode | None = None

    @property
    def allowed_candidates(self) -> list[CandidateAction]:
        return [m.candidate for m in self.masked if m.allowed]

    @property
    def blocked_candidates(self) -> list[MaskedCandidate]:
        return [m for m in self.masked if not m.allowed]


def apply_safety_mask(candidates: list[CandidateAction]) -> SafetyDecision:
    """Assess every candidate and block the ones that fail the risk gate.

    A candidate whose utility/aversion conflict resolves to high risk is never
    silently dropped for the arbiter - it is marked blocked with a reason, so
    upstream policy/telemetry can see *why* (plan §19, §29).
    """
    masked: list[MaskedCandidate] = []
    forced_mode: BehaviorMode | None = None

    for candidate in candidates:
        assessment = assess_risk(candidate)
        allowed = not assessment.is_blocked
        if not allowed:
            candidate = candidate.model_copy(
                update={
                    "is_safe": False,
                    "safety_reason": "; ".join(assessment.reasons) or "high risk",
                }
            )
        elif requires_verification(candidate, assessment):
            forced_mode = BehaviorMode.VERIFY

        masked.append(MaskedCandidate(candidate=candidate, assessment=assessment, allowed=allowed))

    return SafetyDecision(masked=masked, forced_mode=forced_mode)


def filter_safe(candidates: list[CandidateAction]) -> list[CandidateAction]:
    """Convenience wrapper: return only the candidates that pass the mask."""
    return apply_safety_mask(candidates).allowed_candidates
