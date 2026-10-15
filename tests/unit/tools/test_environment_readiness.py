"""Environment tool evidence must describe services, not command exit codes."""

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from flycode.tools import environment_tools


@pytest.mark.parametrize("containers, running", [
    ([], False),
    ([{"State": "exited", "Health": ""}], False),
    ([{"State": "running", "Health": "unhealthy"}], False),
    ([{"State": "running", "Health": "healthy"}], True),
])
def test_compose_ps_reports_actual_readiness(tmp_path, monkeypatch, containers, running):
    # Docker is external; keep the real command parser and result handling.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, json.dumps(containers), ""
    ))
    result = environment_tools.docker_compose(tmp_path, action="ps")
    assert result.success
    assert result.payload["running"] is running


def test_http_failure_contains_status_and_url(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"not ready")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/health"
    try:
        result = environment_tools.service_health(tmp_path, url)
        assert not result.success
        assert "503" in result.error_message
        assert result.payload["url"] == url
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_compose_up_waits_for_health_and_rebuilds(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = environment_tools.docker_compose(tmp_path, action="up")
    assert result.success
    assert "--build" in calls[0]
    assert "--wait" in calls[0]


def test_alembic_can_run_in_compose_service(tmp_path, monkeypatch):
    def run(args, **kwargs):
        assert args == ["docker", "compose", "exec", "-T", "web", "python", "-m", "alembic", "upgrade", "head"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = environment_tools.alembic(tmp_path, action="upgrade", service="web")
    assert result.success
    assert result.payload["migration_applied"] is True


def test_tests_execute_inside_declared_service(tmp_path, monkeypatch):
    from flycode.tools.tests import run_tests

    def run(args, **kwargs):
        assert args == ["docker", "compose", "exec", "-T", "web", "python", "-m", "pytest", "-q"]
        return subprocess.CompletedProcess(args, 0, "2 passed in 0.1s", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = run_tests(tmp_path, service="web")
    assert result.success
    assert result.payload["passed"] == 2
