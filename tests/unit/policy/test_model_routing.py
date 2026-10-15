"""The neural state controls expensive inference, independently of prompts."""

import pytest

from flycode.domain import NeuralState, Observation
from flycode.llm.protocol import CortexContext


def context(mode="execute", confidence=0.3, **kwargs):
    return CortexContext(
        task="fix the bug", behavior_mode=mode,
        observation=kwargs.pop("observation", Observation(task="fix the bug")),
        neural_state=NeuralState(confidence=confidence, behavior_mode=mode, **kwargs),
    )


def test_context_carries_the_actual_neural_state():
    assert context(confidence=0.9).neural_state.confidence == 0.9


@pytest.mark.parametrize("confidence, expected", [(0.2, True), (0.95, False)])
def test_neural_confidence_controls_decider_invocation(confidence, expected):
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(confidence=confidence))
    assert plan.call_reasoner
    assert plan.call_decider is expected
    assert plan.local_candidate is None


def test_blocked_controller_calls_neither_model():
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(is_blocked=True))
    assert not plan.call_reasoner
    assert not plan.call_decider
    assert plan.local_candidate is None


def test_verified_completion_stops_without_inference():
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(observation=Observation(task="fix", tests={"status": "passed"})))
    assert not plan.call_reasoner
    assert not plan.call_decider
    assert plan.local_candidate.tool == "stop"


def test_verify_mode_uses_a_local_test_call():
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(mode="verify"))
    assert not plan.call_reasoner
    assert not plan.call_decider
    assert plan.local_candidate.tool == "run_tests"


def test_compose_verification_requires_semantic_service_selection():
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(mode="verify", observation=Observation(
        task="fix", docker={"required": True, "running": True}
    )))
    assert plan.call_reasoner
    assert plan.local_candidate is None


def test_failed_tests_require_reasoning_instead_of_endless_local_retests():
    from flycode.policy.model_routing import plan_model_calls

    plan = plan_model_calls(context(mode="verify", observation=Observation(
        task="fix", tests={"status": "failed", "failed": 1}
    )))
    assert plan.call_reasoner


def test_hard_control_does_not_request_unused_decider_scores():
    from flycode.policy.model_routing import plan_model_calls

    ctx = context()
    ctx = ctx.model_copy(update={"control_mode": "hard_control"})
    assert not plan_model_calls(ctx).call_decider
