"""The browser API drives real agent tools while keeping local files scoped."""

import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from flycode.web.app import create_app


@pytest.fixture
def client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / "history"), base_url="http://127.0.0.1") as session:
        session.headers["X-FlyCode-Token"] = session.get("/api/bootstrap").json()["token"]
        yield session


def wait_run(client, run_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = client.get(f"/api/runs/{run_id}").json()
        if record["status"] not in ("queued", "running", "stopping"):
            return record
        time.sleep(0.02)
    pytest.fail("run did not finish")


def test_demo_repairs_real_files_and_replays_events_after_refresh(client):
    response = client.post("/api/runs", json={"task": "Fix arithmetic", "llm": "heuristic"})
    assert response.status_code == 201
    run_id = response.json()["id"]
    record = wait_run(client, run_id)
    assert record["status"] == "completed", record
    assert record["result"]["success"]
    events = client.get(f"/api/runs/{run_id}/events").text
    assert '"kind":"tool_start"' in events
    assert '"tool":"edit_file"' in events
    assert '"status":"passed"' in events
    replay = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"}).text
    assert "id: 1\n" not in replay and "id: 2\n" not in replay
    assert '"kind":"finished"' in replay
    assert client.get("/api/runs").json()[0]["id"] == run_id


def test_history_survives_server_restart(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "history"
    with TestClient(create_app(workspace, data), base_url="http://localhost") as first:
        first.headers["X-FlyCode-Token"] = first.get("/api/bootstrap").json()["token"]
        run_id = first.post("/api/runs", json={"task": "fix", "llm": "heuristic"}).json()["id"]
        assert wait_run(first, run_id)["status"] == "completed"
    with TestClient(create_app(workspace, data), base_url="http://localhost") as second:
        assert second.get(f"/api/runs/{run_id}").json()["result"]["success"]
        assert '"kind":"tool_end"' in second.get(f"/api/runs/{run_id}/events").text


@pytest.mark.parametrize("body", [
    {"task": " "}, {"task": "x", "max_steps": 0}, {"task": "x", "llm": "cloud"},
    {"task": "x", "project": "../outside"}, {"task": "x", "project": "/tmp"},
])
def test_invalid_requests_cannot_start_a_run(client, body):
    assert client.post("/api/runs", json=body).status_code in (400, 422)
    assert client.get("/api/runs").json() == []


def test_symlink_project_cannot_escape_workspace(client, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "workspace" / "escape").symlink_to(outside, target_is_directory=True)
    assert client.post("/api/runs", json={"task": "x", "project": "escape"}).status_code == 400


def test_mutations_require_token_and_reject_foreign_origin(client):
    token = client.headers.pop("X-FlyCode-Token")
    assert client.post("/api/runs", json={"task": "x"}).status_code == 403
    assert client.post("/api/runs", json={"task": "x"}, headers={
        "X-FlyCode-Token": token, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 400


def test_model_probe_is_explicit_protected_and_returns_role_evidence(tmp_path, model_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", model_server.url)
    model_server.respond = lambda request: model_server.completion(json.dumps({"candidates": [
        {"tool": "read_file", "arguments": {"path": "sample.py"}}]}))
    with TestClient(create_app(tmp_path, tmp_path / "history"), base_url="http://localhost") as session:
        discovery = session.get("/api/models").json()
        assert not model_server.requests
        entry = discovery["catalogue"][0]
        body = {"model_ref": entry["model_ref"], "role": "primary"}
        assert session.post("/api/models/probe", json=body).status_code == 403
        session.headers["X-FlyCode-Token"] = session.get("/api/bootstrap").json()["token"]
        probed = session.post("/api/models/probe", json=body)
        assert probed.status_code == 200, probed.text
        assert probed.json()["roles"] == ["primary"]
        assert probed.json()["probe_calls"] == 1
        assert len(model_server.requests) == 1
        assert session.post("/api/models/probe", json={**body, "model_ref": "foreign"}).status_code == 400


def test_single_model_preflight_freezes_timeout_and_preserves_null(tmp_path):
    from flycode.web.runs import RunManager, RunRequest

    manager = RunManager(tmp_path, tmp_path / 'data')
    request = RunRequest(task='fix', decision_model=None, request_timeout=37)
    brain, config, _, _ = manager._prepare(request, None)
    try:
        assert config.decision_model is None
        assert config.request_timeout == 37
    finally:
        brain.close()
        manager.close()


def test_stop_stays_responsive_during_inference_and_prevents_edit(client, monkeypatch):
    from flycode.domain import CandidateAction
    from flycode.llm import HeuristicCandidateGenerator
    from flycode.web import runs

    entered, release = Event(), Event()

    class SlowCortex(HeuristicCandidateGenerator):
        def generate_candidates(self, context):
            entered.set()
            assert release.wait(5)
            return [CandidateAction(id="late-edit", tool="create_file", arguments={
                "path": "unexpected.py", "content": "no"})]

    monkeypatch.setattr(runs, "make_lmstudio_cortex", lambda **kwargs: SlowCortex())
    run_id = client.post("/api/runs", json={"task": "fix", "llm": "lmstudio"}).json()["id"]
    try:
        assert entered.wait(5)
        assert client.post("/api/runs", json={"task": "second"}).status_code == 409
        assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "stopping"
    finally:
        release.set()
    record = wait_run(client, run_id)
    assert record["status"] == "stopped"
    assert record["result"]["tool_calls"] == 0


def test_missing_models_are_visible_as_failure(client, monkeypatch):
    from flycode.web import runs

    def missing(**kwargs):
        raise ValueError("Models not available in LM Studio: decider")

    monkeypatch.setattr(runs, "make_lmstudio_cortex", missing)
    run_id = client.post("/api/runs", json={"task": "fix"}).json()["id"]
    record = wait_run(client, run_id)
    assert record["status"] == "failed"
    assert "Models not available" in record["error"]
    assert record["result"] is None


def test_unfinished_history_is_labeled_interrupted(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "history"
    with TestClient(create_app(workspace, data), base_url="http://localhost") as first:
        first.headers["X-FlyCode-Token"] = first.get("/api/bootstrap").json()["token"]
        run_id = first.post("/api/runs", json={"task": "fix", "llm": "heuristic"}).json()["id"]
        wait_run(first, run_id)
    path = data / run_id / "record.json"
    record = json.loads(path.read_text())
    record["status"] = "running"
    path.write_text(json.dumps(record))
    with TestClient(create_app(workspace, data), base_url="http://localhost") as second:
        assert second.get(f"/api/runs/{run_id}").json()["status"] == "interrupted"


def test_model_connection_reports_available_ids(tmp_path, model_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", model_server.url)
    with TestClient(create_app(tmp_path, tmp_path / 'history'), base_url='http://localhost') as session:
        body = session.get("/api/models").json()
        assert body['connected'] and body['error'] == ''
        assert set(body['models']) == {'reasoner', 'decider'}
        assert all(entry['status'] == 'discovered' for entry in body['catalogue'])
        assert not model_server.requests


def test_selected_project_is_never_recreated_as_a_fixture(client, tmp_path, model_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", model_server.url)
    sentinel = tmp_path / "workspace" / "keep.txt"
    sentinel.write_text("Existing user file")
    response = client.post("/api/runs", json={
        "task": "inspect tests", "project": ".", "llm": "lmstudio", "max_steps": 1,
        "model": "reasoner", "decision_model": "decider",
    })
    record = wait_run(client, response.json()["id"])
    assert record["status"] == "incomplete"
    assert sentinel.read_text() == "Existing user file"
    assert not (sentinel.parent / "mathutils.py").exists()


def test_demo_named_user_project_has_a_distinct_selector(client, tmp_path):
    project = tmp_path / "workspace" / "demo"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    projects = client.get("/api/bootstrap").json()["projects"]
    matches = [item for item in projects if item["name"] == "demo"]
    assert len(matches) == 1
    assert matches[0]["id"] != "demo"


def test_full_controller_selection_is_executed_and_preserved(client, tmp_path, monkeypatch):
    import numpy as np

    from flycode.brain.full_connectome.graph import graph_from_edges

    path = tmp_path / 'graph'
    graph_from_edges(np.array([1, 2, 3]), np.array([1, 2]), np.array([2, 3]), np.array([3, 4])).save(path)
    monkeypatch.setenv('FLYCODE_CONNECTOME_PATH', str(path))
    response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic', 'brain': 'full'})
    record = wait_run(client, response.json()['id'])
    assert record['request']['brain'] == 'full'
    assert record['result']['metrics']['brain'] == 'full_connectome'
    assert record['status'] == 'completed'
    assert '"connectome"' in client.get(f"/api/runs/{record['id']}/events").text
