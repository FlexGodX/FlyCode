"""Inference scheduling from controller output (plan §3,23–27)."""

from pydantic import BaseModel

from ..domain import CandidateAction
from ..llm.protocol import CortexContext


class ModelCallPlan(BaseModel):
    call_reasoner: bool = False
    call_decider: bool = False
    local_candidate: CandidateAction | None = None
    reason: str


def plan_model_calls(context: CortexContext) -> ModelCallPlan:
    state = context.neural_state
    obs = context.observation
    if state is not None and state.is_blocked:
        return ModelCallPlan(reason="controller blocked inference")
    if obs.is_healthy:
        return ModelCallPlan(reason="completion verified", local_candidate=CandidateAction(
            id="local-stop", tool="stop", source="fly", rationale="all required checks passed",
        ))
    if (context.behavior_mode == "verify" and obs.tests_status == "unknown"
            and not obs.docker.get("required")):
        return ModelCallPlan(reason="controller requested deterministic verification", local_candidate=CandidateAction(
            id="local-tests", tool="run_tests", source="fly", rationale="verify before requesting inference",
        ))
    confidence = state.confidence if state is not None else 0.5
    return ModelCallPlan(
        call_reasoner=True,
        call_decider=confidence < 0.8 and context.control_mode != "hard_control",
        reason="semantic reasoning required; decision scoring follows neural confidence",
    )
