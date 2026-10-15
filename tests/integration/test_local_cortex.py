"""Local two-model execution retains real routing, policy and tools."""

import json

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import NeuralState, Observation, TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm.protocol import CortexContext
from flycode.policy import ControlMode, run_tournament
from flycode.telemetry import TelemetryLogger


def local_cortex(server, **kwargs):
    from flycode.llm import make_lmstudio_cortex

    return make_lmstudio_cortex(base_url=server.url, model="reasoner", decision_model="decider", **kwargs)


def context(confidence=0.2, **kwargs):
    return CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"),
                         neural_state=NeuralState(confidence=confidence, **kwargs))


def respond_with_candidates(server, actions):
    def respond(request):
        if request["model"] == "decider":
            return server.completion("B", [("A", -2.0), ("B", -0.1)])
        return server.completion(json.dumps({"candidates": actions}))
    server.respond = respond


ACTIONS = [
    {"tool": "read_file", "arguments": {"path": "a.py"}, "rationale": "inspect a"},
    {"tool": "read_file", "arguments": {"path": "b.py"}, "rationale": "inspect b"},
]


def test_uncertain_controller_requests_both_models_and_uses_jev_scores(model_server):
    respond_with_candidates(model_server, ACTIONS)
    cortex = local_cortex(model_server)
    candidates = cortex.generate_candidates(context())
    assert cortex.model_calls == {"reasoner": 1, "decider": 1}
    assert candidates[1].rank_score > candidates[0].rank_score
    result = run_tournament(candidates, {}, ControlMode.BIAS_CONTROL)
    assert result.winner.arguments["path"] == "b.py"
    cortex.consolidate(context(), [])
    assert cortex.model_calls == {"reasoner": 1, "decider": 1}


def test_confident_controller_skips_jev(model_server):
    respond_with_candidates(model_server, ACTIONS)
    cortex = local_cortex(model_server)
    assert len(cortex.generate_candidates(context(confidence=0.99))) == 2
    assert cortex.model_calls == {"reasoner": 1, "decider": 0}


def test_blocked_controller_does_not_infer(model_server):
    cortex = local_cortex(model_server)
    assert cortex.generate_candidates(context(is_blocked=True)) == []
    assert not model_server.requests
    assert sum(cortex.model_calls.values()) == 0


def test_decider_failure_retains_reasoner_candidates_with_a_diagnostic(model_server):
    def respond(request):
        return ((503, {"error": {"message": "unavailable", "type": "server_error"}})
                if request["model"] == "decider" else model_server.completion(json.dumps(ACTIONS)))

    model_server.respond = respond
    cortex = local_cortex(model_server)
    candidates = cortex.generate_candidates(context())
    assert len(candidates) == 2
    assert all(candidate.rank_score is None for candidate in candidates)
    assert "decider" in cortex.last_routing.error
    assert cortex.model_calls == {"reasoner": 1, "decider": 1}


def test_reasoner_failure_never_invents_a_repair(model_server):
    model_server.respond = lambda request: (503, {"error": {"message": "unavailable", "type": "server_error"}})
    cortex = local_cortex(model_server)
    assert cortex.generate_candidates(context()) == []
    assert "reasoner" in cortex.last_routing.error
    assert cortex.model_calls == {"reasoner": 1, "decider": 0}


def test_model_budget_counts_requests_and_can_be_reset(model_server):
    respond_with_candidates(model_server, ACTIONS)
    cortex = local_cortex(model_server, max_model_calls=1)
    assert len(cortex.generate_candidates(context())) == 2
    assert cortex.generate_candidates(context()) == []
    assert cortex.model_calls == {"reasoner": 1, "decider": 0}
    assert "budget" in cortex.last_routing.error
    cortex.reset()
    assert len(cortex.generate_candidates(context())) == 2
    assert cortex.model_calls == {"reasoner": 1, "decider": 0}


def test_unsafe_candidates_never_reach_jev_or_win_selection(model_server):
    unsafe = {"tool": "postgres_query", "arguments": {"query": "DROP DATABASE app"}}
    respond_with_candidates(model_server, [unsafe, *ACTIONS])
    cortex = local_cortex(model_server)
    candidates = cortex.generate_candidates(context())
    decision_request = next(request for request in model_server.requests if request["model"] == "decider")
    assert "DROP DATABASE" not in decision_request["messages"][0]["content"]
    result = run_tournament(candidates, {candidates[0].id: 100}, ControlMode.HARD_CONTROL)
    assert result.winner.tool == "read_file"
    assert candidates[0].id in result.blocked


def test_final_brain_scores_can_override_jev(model_server):
    respond_with_candidates(model_server, ACTIONS)
    candidates = local_cortex(model_server).generate_candidates(context())
    result = run_tournament(candidates, {candidates[0].id: 1, candidates[1].id: -1})
    assert result.winner.arguments["path"] == "a.py"


def test_missing_model_is_reported_before_inference(model_server):
    model_server.models = ["reasoner"]
    with pytest.raises(ValueError, match="decider"):
        local_cortex(model_server)
    assert not model_server.requests


async def test_local_cortex_repairs_toy_repo_and_records_actual_calls(tmp_path, model_server):
    reasoner_turn = 0

    def respond(request):
        nonlocal reasoner_turn
        if request["model"] == "decider":
            return model_server.completion("A", [("A", -0.1), ("B", -2.0)])
        reasoner_turn += 1
        if reasoner_turn == 1:
            actions = [{"tool": "read_file", "arguments": {"path": "mathutils.py"}}]
        else:
            actions = [{"tool": "edit_file", "arguments": {
                "path": "mathutils.py", "old_text": "a + b + 1", "new_text": "a + b"}},
                       {"tool": "git_status", "arguments": {}}]
        return model_server.completion(json.dumps({"candidates": actions}))

    model_server.respond = respond
    cortex = local_cortex(model_server)
    logger = TelemetryLogger(tmp_path / "telemetry")
    result = await run_agent(SoftwareEnvironment(tmp_path / "repo"), make_brain("functional", seed=0), cortex,
                             TaskSpec(id="local", task="fix", fixture="toy_arithmetic_bug", max_steps=15),
                             telemetry=logger)
    assert result.success, result.transcript
    assert result.metrics.llm_calls == len(model_server.requests)
    assert result.metrics.model_calls["reasoner"] >= 2
    assert result.metrics.model_calls["decider"] >= 1
    steps = logger.read_steps("run")
    assert all(step.model_routing for step in steps)
    assert any(step.model_routing["route"] == "local" for step in steps)
    assert "local-stop" in next(step.chosen_action["id"] for step in steps if step.chosen_action["tool"] == "stop")


def test_local_clients_ignore_cloud_settings_and_proxy_environment(model_server, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    respond_with_candidates(model_server, ACTIONS)
    cortex = local_cortex(model_server)
    assert len(cortex.generate_candidates(context())) == 2
    assert cortex.model_calls == {"reasoner": 1, "decider": 1}


def test_candidate_count_is_bounded_before_decision_scoring(model_server):
    respond_with_candidates(model_server, ACTIONS * 5)
    cortex = local_cortex(model_server)
    candidates = cortex.generate_candidates(context())
    assert len(candidates) == 4
    request = next(request for request in model_server.requests if request["model"] == "decider")
    assert "E. " not in request["messages"][0]["content"]
