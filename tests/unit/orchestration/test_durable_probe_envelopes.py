"""Malformed successful HTTP responses revoke the previous protocol admission."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from flycode.domain import Observation
from flycode.llm.catalogue import CatalogueBusy, ModelCatalogue
from flycode.llm.openai_compatible import OpenAICompatibleLLM
from flycode.llm.protocol import CortexContext
from flycode.orchestration.store import Store
from flycode.orchestration.transport import HostTransport


@pytest.mark.parametrize('malformed', ['empty_choices', 'null_choices', 'null_message', 'object_content',
                                    'nonempty_object_content', 'object_choices'])
def test_malformed_primary_reprobe_revokes_previous_admission(tmp_path, model_server, malformed):
    database = tmp_path / 'catalogue.sqlite'
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    catalogue = ModelCatalogue(model_server.url, database=database)
    ready = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    assert ready.roles == ['primary']
    response = model_server.completion('invalid')
    if malformed == 'empty_choices':
        response['choices'] = []
    elif malformed == 'null_choices':
        response['choices'] = None
    elif malformed == 'object_choices':
        response['choices'] = {}
    elif malformed == 'null_message':
        response['choices'][0]['message'] = None
    else:
        response['choices'][0]['message']['content'] = {} if malformed == 'object_content' else {'invalid': True}
    model_server.respond = lambda request: response
    rejected = catalogue.probe(ready.model_ref, 'primary')
    assert rejected.status == 'unsupported' and not rejected.roles
    restored = ModelCatalogue(model_server.url, database=database)
    assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
    assert len(model_server.requests) == 2
    store = Store(database)
    try:
        assert store.db.execute("SELECT COUNT(*) FROM model_calls WHERE state='completed'").fetchone()[0] == 2
    finally:
        store.close()


def test_busy_endpoint_does_not_lose_observed_configuration_revocation(tmp_path, model_server):
    database = tmp_path / 'catalogue.sqlite'
    completion = model_server.completion(json.dumps({'candidates': [{'tool': 'list_files'}]}))
    model_server.respond = lambda request: completion
    catalogue = ModelCatalogue(model_server.url, database=database)
    ready = catalogue.probe(catalogue.discover()[0].model_ref, 'primary')
    entered, release = Event(), Event()
    def respond(request):
        entered.set()
        assert release.wait(5)
        return completion
    model_server.respond = respond
    transport = HostTransport(model_server.url, ready.loaded_instance_id, 'primary', database=database)
    client = OpenAICompatibleLLM(model=ready.loaded_instance_id, base_url=model_server.url, api_key='local-test',
        structured_output=True, request_executor=lambda request: transport.execute(request, timeout=0))
    context = CortexContext(task='Inspect sample.py', behavior_mode='explore', n_candidates=1,
                            observation=Observation(task='busy endpoint'))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.generate_candidates, context)
        try:
            assert entered.wait(5)
            model_server.contexts = {name: 4096 for name in model_server.models}
            with pytest.raises(CatalogueBusy, match='occupied'):
                catalogue.probe(ready.model_ref, 'primary')
            restored = ModelCatalogue(model_server.url, database=database)
            assert restored.resolve(ready.model_ref, 'primary', ready.capability_version) is None
        finally:
            release.set()
        assert pending.result(timeout=5)
    assert len(model_server.requests) == 2
