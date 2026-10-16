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


def test_transport_executors_guard_both_models_and_reject_half_guarded_pair(model_server):
    from flycode.llm import make_lmstudio_cortex

    respond_with_candidates(model_server, ACTIONS)
    admitted = []
    def primary(request):
        admitted.append('primary')
        return request()
    def scorer(request):
        raise InterruptedError('Scorer not admitted')
    with pytest.raises(ValueError, match='executor'):
        local_cortex(model_server, primary_request_executor=primary)
    assert model_server.requests == []
    cortex = local_cortex(model_server, primary_request_executor=primary, decision_request_executor=scorer)
    with pytest.raises(InterruptedError):
        cortex.generate_candidates(context())
    assert admitted == ['primary']
    assert cortex.model_calls == {'reasoner': 1, 'decider': 0}
    assert [request['model'] for request in model_server.requests] == ['reasoner']
    single = make_lmstudio_cortex(base_url=model_server.url, model='reasoner', decision_model=None,
                                primary_request_executor=primary)
    assert len(single.generate_candidates(context())) == 2
    assert single.model_calls == {'reasoner': 1}


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


def test_single_model_needs_no_decider_and_preserves_routing_and_budget(model_server):
    from flycode.llm import make_lmstudio_cortex

    model_server.models = ["reasoner"]
    respond_with_candidates(model_server, ACTIONS)
    events = []
    cortex = make_lmstudio_cortex(base_url=model_server.url, model="reasoner", decision_model=None,
                                 max_model_calls=1, on_event=lambda kind, data: events.append((kind, data)))
    assert len(cortex.generate_candidates(context())) == 2
    assert cortex.model_calls == {"reasoner": 1}
    assert cortex.last_routing.route == "reasoner"
    assert all(data["role"] == "reasoner" for _, data in events)
    assert cortex.generate_candidates(context()) == []
    assert len(model_server.requests) == 1
    cortex.reset()
    assert cortex.model_calls == {"reasoner": 0}
    assert cortex.generate_candidates(context(is_blocked=True)) == []
    healthy = context().model_copy(update={"observation": Observation(
        task="fix", tests={"status": "passed", "passed": 1})})
    assert cortex.generate_candidates(healthy)[0].tool == "stop"
    assert len(model_server.requests) == 1


@pytest.mark.parametrize("bad", ["", " ", "none", "null"])
def test_model_selection_rejects_string_sentinels(bad):
    from flycode.research.cortex import CortexConfig
    from flycode.web.runs import RunRequest

    for contract in (CortexConfig, RunRequest):
        with pytest.raises(ValueError):
            contract(**({"task": "fix"} if contract is RunRequest else {}), decision_model=bad)


def test_null_selection_survives_serialization_without_changing_legacy_default():
    from flycode.research.cortex import CortexConfig
    from flycode.research.validation import cortex_identity
    from flycode.web.runs import RunRequest

    request = RunRequest(task="fix", decision_model=None)
    assert RunRequest.model_validate_json(request.model_dump_json()).decision_model is None
    assert RunRequest(task="fix").decision_model == "jev-style-qwen3.5-2b-decision"
    single = CortexConfig(cortex="lmstudio", decision_model=None)
    assert cortex_identity(single)["decision_model"] is None
    assert cortex_identity(single) != cortex_identity(CortexConfig(cortex="lmstudio"))


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


def test_model_events_bracket_actual_requests(model_server):
    respond_with_candidates(model_server, ACTIONS)
    events = []
    cortex = local_cortex(model_server, on_event=lambda kind, data: events.append(
        (kind, data, len(model_server.requests))))
    cortex.generate_candidates(context())
    assert [(kind, data["role"], count) for kind, data, count in events] == [
        ("model_start", "reasoner", 0), ("model_end", "reasoner", 1),
        ("model_start", "decider", 1), ("model_end", "decider", 2),
    ]


def test_stop_after_reasoner_prevents_decider_request(model_server):
    respond_with_candidates(model_server, ACTIONS)
    stopped = False

    def receive(kind, data):
        nonlocal stopped
        if kind == "model_end" and data["role"] == "reasoner":
            stopped = True

    cortex = local_cortex(model_server, on_event=receive, should_stop=lambda: stopped)
    assert len(cortex.generate_candidates(context())) == 2
    assert cortex.model_calls == {"reasoner": 1, "decider": 0}
