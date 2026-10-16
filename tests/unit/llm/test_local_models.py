"""Real HTTP clients must obey the distinct Qwen and Jev wire protocols."""

import json
import math
import time

import pytest

from flycode.domain import CandidateAction, Observation
from flycode.llm.openai_compatible import OpenAICompatibleLLM, _parse_candidates
from flycode.llm.protocol import CortexContext


@pytest.mark.parametrize("raw", ['{}', 'null', '5', '[null, 3]', '[{"tool":"read_file","confidence":2}]'])
def test_bad_candidate_shapes_are_rejected(raw):
    assert _parse_candidates(raw, ["read_file"]) == []


def test_structured_reasoner_request_and_candidate_validation(model_server):
    model_server.respond = lambda request: model_server.completion(json.dumps({"candidates": [
        {"tool": "read_file", "arguments": {"path": "mathutils.py"}, "rationale": "inspect implementation"}
    ]}))
    client = OpenAICompatibleLLM(model="reasoner", base_url=model_server.url, api_key="local",
                                 structured_output=True, max_tokens=1024, timeout=3)
    ctx = CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"))
    result = client.generate_candidates(ctx)
    assert result[0].arguments == {"path": "mathutils.py"}
    request = model_server.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["max_tokens"] == 1024
    assert client.request_count == 1


def test_standalone_observation_keeps_its_distinct_task(model_server):
    client = OpenAICompatibleLLM(model='reasoner', base_url=model_server.url, api_key='local')
    context = CortexContext(task='Repair the parser.', behavior_mode='diagnose',
        observation=Observation(task='Observed subtask identity.', task_id='legacy-task'))
    client.generate_candidates(context)
    prompt = model_server.requests[-1]['messages'][-1]['content']
    assert 'Repair the parser.' in prompt and 'Observed subtask identity.' in prompt and 'legacy-task' in prompt


def test_jev_uses_one_token_probabilities_and_candidate_ids(model_server):
    from flycode.llm.decision import JevStyleDecisionClient

    model_server.respond = lambda request: model_server.completion(" A", [(" A", math.log(0.8)), (" B", math.log(0.2))])
    client = JevStyleDecisionClient(model="decider", base_url=model_server.url, api_key="local", timeout=3)
    ctx = CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"))
    candidates = [CandidateAction(id="read-a", tool="read_file", arguments={"path": "a.py"}),
                  CandidateAction(id="read-b", tool="read_file", arguments={"path": "b.py"})]
    scores = client.score(ctx, candidates)
    assert scores == pytest.approx({"read-a": 0.8, "read-b": 0.2})
    request = model_server.requests[0]
    assert request["max_tokens"] == 1
    assert request["logprobs"] is True
    assert request["top_logprobs"] == 20
    assert request["messages"][0]["content"].endswith("Answer:")
    assert "a.py" in request["messages"][0]["content"]
    assert client.request_count == 1


@pytest.mark.parametrize("tokens", [None, [("Z", -1.0)], [("A", float('nan')), ("B", -1.0)]])
def test_jev_rejects_unusable_distributions(model_server, tokens):
    from flycode.llm.decision import JevStyleDecisionClient

    model_server.respond = lambda request: model_server.completion("A", tokens)
    client = JevStyleDecisionClient(model="decider", base_url=model_server.url, api_key="local")
    ctx = CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"))
    with pytest.raises(ValueError):
        client.score(ctx, [CandidateAction(id="a", tool="read_file"), CandidateAction(id="b", tool="run_tests")])


def test_jev_duplicate_letter_variants_do_not_duplicate_an_option(model_server):
    from flycode.llm.decision import JevStyleDecisionClient

    model_server.respond = lambda request: model_server.completion("A", [("A", -1.0), (" A", -2.0), ("B", -1.0)])
    client = JevStyleDecisionClient(model="decider", base_url=model_server.url, api_key="local")
    ctx = CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"))
    scores = client.score(ctx, [CandidateAction(id="a", tool="read_file"), CandidateAction(id="b", tool="run_tests")])
    assert set(scores) == {"a", "b"}
    assert sum(scores.values()) == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in scores.values())


def test_timeout_counts_one_attempt_without_automatic_retries(model_server):
    from openai import APITimeoutError

    def slow(request):
        time.sleep(0.1)
        return model_server.completion("[]")

    model_server.respond = slow
    client = OpenAICompatibleLLM(model="reasoner", base_url=model_server.url, api_key="local", timeout=0.02)
    ctx = CortexContext(task="fix", behavior_mode="execute", observation=Observation(task="fix"))
    with pytest.raises(APITimeoutError):
        client.generate_candidates(ctx)
    assert client.request_count == 1
    assert len(model_server.requests) == 1


def test_scorer_transport_gate_prevents_unadmitted_http_and_counter_increment(model_server):
    from flycode.llm.decision import JevStyleDecisionClient

    permitted = [False]
    def gate(request):
        if not permitted[0]:
            raise InterruptedError('Not admitted')
        return request()
    model_server.respond = lambda request: model_server.completion('A', [('A', -1.), ('B', -1.)])
    client = JevStyleDecisionClient(model='decider', base_url=model_server.url, api_key='local', request_executor=gate)
    context = CortexContext(task='Inspect the parser.', behavior_mode='execute', observation=Observation(task='Inspect'))
    candidates = [CandidateAction(id='a', tool='read_file'), CandidateAction(id='b', tool='run_tests')]
    with pytest.raises(InterruptedError):
        client.score(context, candidates)
    assert client.request_count == 0 and model_server.requests == []
    permitted[0] = True
    assert client.score(context, candidates) == pytest.approx({'a': .5, 'b': .5})
    assert client.request_count == 1 and len(model_server.requests) == 1
    assert model_server.requests[0]['logprobs'] is True
