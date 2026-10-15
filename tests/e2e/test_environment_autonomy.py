"""Phase 4 acceptance through the real agent, tools, Docker, DB and HTTP."""

import asyncio
import socket
import subprocess

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment, fixtures
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode
from flycode.tools.environment_tools import docker_compose, http_request


def _toolchain_ready():
    try:
        for args in (["docker", "compose", "version"], ["docker", "info"]):
            if subprocess.run(args, capture_output=True, timeout=10, check=False).returncode != 0:
                return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _toolchain_ready(), reason="requires Docker and Docker Compose")


@pytest.fixture
def isolated_demo(monkeypatch):
    # Change only the external port binding; all five defects remain present.
    # The agent sees the expected URL in README, never the repair metadata.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    files = fixtures._FASTAPI_POSTGRES_APP_FILES
    monkeypatch.setitem(files, "README.md", files["README.md"].replace(":8000", f":{port}"))
    urls = [f"http://localhost:{port}/health", f"http://localhost:{port}/users"]
    return urls


@pytest.mark.parametrize("control", [ControlMode.BIAS_CONTROL, ControlMode.HARD_CONTROL])
async def test_agent_repairs_and_runs_fastapi_postgres(tmp_path, isolated_demo, control):
    repo = tmp_path / "phase4"
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="phase4", task="Fix this application and get it fully running",
                    fixture="fastapi_postgres_app", max_steps=60, max_tool_calls=60,
                    success_criteria={"service_urls": isolated_demo})
    try:
        result = await run_agent(env, make_brain("functional", seed=0), HeuristicCandidateGenerator(),
                                 task, control_mode=control)
        assert result.success, "\n".join(result.transcript)
        assert result.metrics.verified_completion
        obs = await env.observe()
        assert obs.tests["passed"] == 2
        assert obs.database["migration_applied"] is True
        assert obs.database["connected"] is True
        assert obs.docker["running"] is True
        assert all(http_request(repo, url).success for url in isolated_demo)
        version = await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "appdb",
             "-Atc", "SELECT version_num FROM alembic_version"],
            cwd=repo, capture_output=True, text=True, timeout=20, check=False,
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.strip() == "001"
        assert result.transcript[-1].split("] ", 1)[1].startswith("stop(")
    finally:
        if repo.exists():
            stopped = docker_compose(repo, action="down")
            assert stopped.success, stopped.error_message
