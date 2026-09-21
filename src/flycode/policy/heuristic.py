"""LLM-only policy - the "no fly" baseline (plan §61).

Ranks candidates purely on the LLM's own predictions after the safety mask,
with zero fly bias. Used as the control condition every fly-derived
controller must beat.
"""

from ..domain import CandidateAction
from .bias_control import BiasControlResult, rank_candidates


def select_llm_only(candidates: list[CandidateAction]) -> BiasControlResult:
    return rank_candidates(candidates, fly_bias={})
