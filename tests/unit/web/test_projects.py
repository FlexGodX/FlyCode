"""Local project registration preserves folders and scopes agent runs."""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient
from test_app import wait_run

from flycode.web.app import create_app


@pytest.fixture
def client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / "history"), base_url="http://localhost") as session:
        session.headers["X-FlyCode-Token"] = session.get("/api/bootstrap").json()["token"]
        yield session


def test_register_external_folder_and_restore_after_restart(client, tmp_path):
    folder = tmp_path / "Local notes"
    folder.mkdir()
    (folder / "keep.txt").write_text("existing content")
    response = client.post("/api/projects", json={"name": "Notes", "path": str(folder)})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["name"] == "Notes"
    assert project["description"] == str(folder)
    assert (folder / "keep.txt").read_text() == "existing content"
    assert not (folder / ".git").exists()
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "history"),
                    base_url="http://localhost") as restarted:
        assert project in restarted.get("/api/bootstrap").json()["projects"]
        assert restarted.app.state.runs.project_path(project["id"]) == folder


def test_create_new_folder_without_git(client, tmp_path):
    folder = tmp_path / "New folder"
    response = client.post("/api/projects", json={"name": "New", "path": str(folder), "create": True})
    assert response.status_code == 201, response.text
    assert folder.is_dir()
    assert list(folder.iterdir()) == []
    assert response.json() in client.get("/api/bootstrap").json()["projects"]
    assert client.post("/api/projects", json={
        "name": "Overwrite", "path": str(folder), "create": True,
    }).status_code == 400


def test_duplicate_paths_reuse_project_identity(client, tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    first = client.post("/api/projects", json={"name": "Notes", "path": str(folder)}).json()
    second = client.post("/api/projects", json={"name": "Again", "path": str(folder / ".")}).json()
    assert second == first
    assert sum(item["id"] == first["id"] for item in client.get("/api/bootstrap").json()["projects"]) == 1


@pytest.mark.parametrize("body", [
    {"name": " ", "path": "/tmp"}, {"name": "x", "path": ""},
    {"name": "x", "path": "missing"}, {"name": "x", "path": "../outside", "create": True},
])
def test_invalid_projects_do_not_create_folders(client, body, tmp_path):
    assert client.post("/api/projects", json=body).status_code in (400, 422)
    assert not (tmp_path / "outside").exists()


def test_project_registration_requires_local_session(client, tmp_path):
    body = {"name": "x", "path": str(tmp_path / "unwanted"), "create": True}
    token = client.headers.pop("X-FlyCode-Token")
    assert client.post("/api/projects", json=body).status_code == 403
    assert client.post("/api/projects", json=body, headers={
        "X-FlyCode-Token": token, "Origin": "https://foreign.example",
    }).status_code == 403
    assert not (tmp_path / "unwanted").exists()


def test_registered_folder_cannot_be_retargeted(client, tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    project = client.post("/api/projects", json={"name": "Notes", "path": str(folder)}).json()
    folder.rmdir()
    assert client.post("/api/runs", json={"task": "inspect", "project": project["id"]}).status_code == 400
    folder.symlink_to(tmp_path / "workspace", target_is_directory=True)
    assert client.post("/api/runs", json={"task": "inspect", "project": project["id"]}).status_code == 400


def test_two_chats_execute_in_registered_non_git_folder(client, tmp_path, model_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", model_server.url)
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "note.txt").write_text("hello")
    (folder / "test_note.py").write_text(
        'from pathlib import Path\n\ndef test_note():\n'
        '    assert Path("note.txt").read_text() == "hello"\n'
    )
    project = client.post("/api/projects", json={"name": "Plain", "path": str(folder)}).json()
    model_server.respond = lambda request: model_server.completion(json.dumps({"candidates": [
        {"tool": "read_file", "arguments": {"path": "note.txt"}},
    ]}))
    ids = []
    for task in ("Read first", "Read second"):
        response = client.post("/api/runs", json={
            "task": task, "project": project["id"], "max_steps": 1,
            "model": "reasoner", "decision_model": None,
        })
        assert response.status_code == 201, response.text
        record = wait_run(client, response.json()["id"])
        ids.append(record["id"])
        assert record["status"] == "completed", record
        events = client.get(f"/api/runs/{record['id']}/events").text
        assert '"status":"passed"' in events
        assert '"passed":1' in events
    assert ids[0] != ids[1]
    assert [run["request"]["project"] for run in client.get("/api/runs").json()] == [project["id"]] * 2
    assert not (folder / ".git").exists()


def test_chats_without_project_get_separate_empty_folders(client, tmp_path, model_server, monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", model_server.url)
    ids = []
    for task in ("First independent chat", "Second independent chat"):
        response = client.post("/api/runs", json={
            "task": task, "project": None, "max_steps": 1,
            "model": "reasoner", "decision_model": None,
        })
        assert response.status_code == 201, response.text
        record = wait_run(client, response.json()["id"])
        assert record["request"]["project"] is None
        assert record["status"] == "incomplete", record
        folder = tmp_path / "history" / record["id"] / "repository"
        assert folder.is_dir()
        assert not (folder / "mathutils.py").exists()
        assert not (folder / ".git").exists()
        ids.append(record["id"])
    assert ids[0] != ids[1]
    assert list((tmp_path / "workspace").iterdir()) == []
    with TestClient(create_app(tmp_path / "workspace", tmp_path / "history"),
                    base_url="http://localhost") as restarted:
        history = restarted.get("/api/runs").json()
        assert {run["id"] for run in history} == set(ids)
        assert all(run["request"]["project"] is None for run in history)


@pytest.mark.parametrize("mode", ["existing", "create"])
def test_native_folder_picker_returns_path_without_registering(client, tmp_path, monkeypatch, mode):
    folder = tmp_path / 'Picked folder'
    folder.mkdir()
    name = 'Project "quoted" $(literal)'

    def native_dialog(command, **kwargs):
        assert not kwargs.get("shell")
        assert command[-3:] == [mode, name, str(tmp_path / "workspace")]
        return subprocess.CompletedProcess(command, 0, json.dumps(str(folder)))

    monkeypatch.setattr(subprocess, "run", native_dialog)
    response = client.post("/api/projects/choose-folder", json={"mode": mode, "name": name})
    assert response.status_code == 200, response.text
    assert response.json() == {"path": str(folder)}
    assert not (tmp_path / "history" / "projects.json").exists()


def test_cancelled_native_picker_returns_no_path(client, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "null"))
    response = client.post("/api/projects/choose-folder", json={})
    assert response.status_code == 200
    assert response.json() == {"path": None}


def test_native_picker_failure_keeps_manual_entry_available(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("No graphical session")

    monkeypatch.setattr(subprocess, "run", unavailable)
    response = client.post("/api/projects/choose-folder", json={})
    assert response.status_code == 503
    assert isinstance(response.json()["detail"], str)


def test_native_picker_requires_local_session(client):
    token = client.headers.pop("X-FlyCode-Token")
    assert client.post("/api/projects/choose-folder", json={}).status_code == 403
    assert client.post("/api/projects/choose-folder", json={}, headers={
        "X-FlyCode-Token": token, "Origin": "https://foreign.example",
    }).status_code == 403
