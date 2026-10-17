"""Actual explicit probe admissions survive independent application instances."""

import json

from fastapi.testclient import TestClient

from flycode.web.app import create_app


def test_explicit_model_probe_survives_restart_without_another_request(tmp_path, model_server, monkeypatch):
    monkeypatch.setenv('LMSTUDIO_BASE_URL', model_server.url)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [
        {'tool': 'read_file', 'arguments': {'path': 'sample.py'}}]}))
    with TestClient(create_app(tmp_path, tmp_path / 'first'), base_url='http://localhost') as first:
        entry = first.get('/api/models').json()['catalogue'][0]
        first.headers['X-FlyCode-Token'] = first.get('/api/bootstrap').json()['token']
        response = first.post('/api/models/probe', json={'model_ref': entry['model_ref'], 'role': 'primary'})
        assert response.status_code == 200, response.text
        assert response.json()['roles'] == ['primary']
    with TestClient(create_app(tmp_path, tmp_path / 'second'), base_url='http://localhost') as second:
        restored = next(item for item in second.get('/api/models').json()['catalogue']
                        if item['model_ref'] == entry['model_ref'])
        assert restored['roles'] == ['primary']
        assert restored['revision'] == response.json()['revision']
        assert restored['probe_calls'] == 1
    assert len(model_server.requests) == 1
