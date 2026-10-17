"""Discovery never fabricates support for either model protocol."""

import json

import pytest


def test_discovery_does_not_infer_or_claim_capabilities(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    catalogue = ModelCatalogue(model_server.url)
    entries = catalogue.discover()
    assert {entry.model_id for entry in entries} == {"reasoner", "decider"}
    assert all(entry.status == "discovered" and not entry.roles for entry in entries)
    assert not model_server.requests
    assert all(entry.model_ref != entry.model_id for entry in entries)


def test_primary_probe_uses_real_protocol_and_disappearance_clears_readiness(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.respond = lambda request: model_server.completion(json.dumps({"candidates": [
        {"tool": "read_file", "arguments": {"path": "sample.py"}}
    ]}))
    catalogue = ModelCatalogue(model_server.url)
    primary = catalogue.discover()[0]
    result = catalogue.probe(primary.model_ref, "primary")
    assert result.status == "ready" and result.roles == ["primary"]
    assert result.probe_calls == 1
    assert result.weights_revision is None
    assert model_server.requests[0]["response_format"]["type"] == "json_schema"
    model_server.models = []
    missing = catalogue.discover()[0]
    assert missing.status == "unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        catalogue.probe(primary.model_ref, "primary")
    assert len(model_server.requests) == 1


def test_scorer_probe_requires_option_logprobs_and_never_marks_failure_ready(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.discover()[0]
    model_server.respond = lambda request: model_server.completion("A")
    rejected = catalogue.probe(entry.model_ref, "scorer")
    assert rejected.status == "unsupported" and not rejected.roles
    assert rejected.probe_calls == 1 and rejected.reason
    model_server.respond = lambda request: model_server.completion("A", [("A", -.1), ("B", -2.)])
    accepted = catalogue.probe(entry.model_ref, "scorer")
    assert accepted.status == "ready" and accepted.roles == ["scorer"]
    assert accepted.probe_calls == 2
    assert len(model_server.requests) == 2
    with pytest.raises(ValueError):
        catalogue.probe("foreign-ref", "primary")


def test_resolution_requires_current_role_version_and_returns_detached_snapshot(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.discover()[0]
    assert catalogue.resolve(entry.model_ref, 'primary', entry.capability_version) is None
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    ready = catalogue.probe(entry.model_ref, 'primary')
    resolved = catalogue.resolve(entry.model_ref, 'primary', entry.capability_version)
    assert resolved.revision > entry.revision
    resolved.roles.clear()
    assert catalogue.resolve(entry.model_ref, 'primary', entry.capability_version).roles == ['primary']
    assert catalogue.resolve(entry.model_ref, 'scorer', entry.capability_version) is None
    assert catalogue.resolve(entry.model_ref, 'primary', 'obsolete') is None
    assert catalogue.lock.acquire(blocking=False)
    try:
        assert catalogue.resolve(entry.model_ref, 'primary', entry.capability_version) is None
    finally:
        catalogue.lock.release()
    assert len(model_server.requests) == ready.probe_calls == 1


def test_runtime_binding_requires_loaded_instance_and_records_its_actual_context(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.contexts = {'reasoner': 8192, 'decider': None}
    catalogue = ModelCatalogue(model_server.url)
    entries = {entry.model_id: entry for entry in catalogue.discover()}
    unloaded = catalogue.probe(entries['decider'].model_ref, 'primary')
    assert unloaded.status == 'unavailable'
    assert unloaded.loaded_instance_id is None and model_server.requests == []
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    ready = catalogue.probe(entries['reasoner'].model_ref, 'primary')
    assert ready.loaded_instance_id == 'reasoner' and ready.context_limit == 8192
    assert ready.load_fingerprint and len(model_server.requests) == 1
    model_server.contexts['reasoner'] = 4096
    changed = catalogue.refresh_runtime(ready.model_ref)
    assert changed.context_limit == 4096 and changed.load_fingerprint != ready.load_fingerprint
    assert changed.revision > ready.revision
    assert len(model_server.requests) == 1


def test_missing_native_metadata_does_not_guess_context_or_infer(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.native_available = False
    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.discover()[0]
    result = catalogue.probe(entry.model_ref, 'primary')
    assert result.status == 'unavailable' and not result.roles
    assert result.context_limit is None and not model_server.requests


def test_retargeted_loaded_instance_loses_role_until_explicit_probe(model_server):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    model_server.instance_ids[entry.model_id] = 'new-loaded-instance'
    changed = catalogue.refresh_runtime(entry.model_ref)
    assert changed.loaded_instance_id == 'new-loaded-instance'
    assert not changed.roles and changed.status == 'discovered'
    assert catalogue.resolve(entry.model_ref, 'primary', entry.capability_version) is None
    assert len(model_server.requests) == 1
    ready = catalogue.probe(entry.model_ref, 'primary')
    assert ready.roles == ['primary'] and ready.status == 'ready'
    assert model_server.requests[-1]['model'] == 'new-loaded-instance'


@pytest.mark.parametrize('capacity', [None, 0, -1, True, '8192'])
def test_missing_or_invalid_loaded_capacity_never_sends_probe(model_server, capacity):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.contexts = {'reasoner': capacity, 'decider': capacity}
    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    assert entry.status == 'unavailable' and entry.context_limit is None
    assert model_server.requests == []


def test_primary_probe_requests_one_candidate_without_changing_generation_default(model_server):
    from flycode.domain import Observation
    from flycode.llm.catalogue import ModelCatalogue
    from flycode.llm.openai_compatible import OpenAICompatibleLLM
    from flycode.llm.protocol import CortexContext

    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.discover()[0]
    ready = catalogue.probe(entry.model_ref, 'primary')
    assert ready.status == 'ready' and ready.roles == ['primary'] and ready.probe_calls == 1
    probe_schema = model_server.requests[0]['response_format']['json_schema']['schema']['properties']['candidates']
    assert probe_schema['minItems'] == probe_schema['maxItems'] == 1
    context = CortexContext(task='Inspect sample.py without changing it.', behavior_mode='explore',
        observation=Observation(task='ordinary generation'))
    assert context.n_candidates == 4
    generator = OpenAICompatibleLLM(model='reasoner', base_url=model_server.url, api_key='test', structured_output=True)
    assert generator.generate_candidates(context)
    ordinary_schema = model_server.requests[1]['response_format']['json_schema']['schema']['properties']['candidates']
    assert ordinary_schema['maxItems'] == context.n_candidates == 4
    assert len(model_server.requests) == 2


@pytest.mark.parametrize('reply', ['', '{}', '{"candidates": []}', '{"candidates": [',
    '{"candidates": [{"tool": "unsupported", "arguments": {}}]}'])
def test_single_candidate_probe_still_rejects_empty_malformed_or_unsupported_output(model_server, reply):
    from flycode.llm.catalogue import ModelCatalogue

    model_server.respond = lambda request: model_server.completion(reply)
    catalogue = ModelCatalogue(model_server.url)
    entry = catalogue.discover()[0]
    rejected = catalogue.probe(entry.model_ref, 'primary')
    assert rejected.status == 'unsupported' and not rejected.roles and rejected.probe_calls == 1
    assert catalogue.resolve(entry.model_ref, 'primary', entry.capability_version) is None
    schema = model_server.requests[0]['response_format']['json_schema']['schema']['properties']['candidates']
    assert schema['maxItems'] == 1 and len(model_server.requests) == 1
