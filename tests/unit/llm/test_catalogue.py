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
