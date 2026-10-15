"""Tests for SoftwareGym: deterministic fixtures and the reset/step contract (plan §6-8)."""

import pytest

from flycode.domain import TaskSpec, ToolAction
from flycode.environment import SoftwareEnvironment


@pytest.fixture
def repo(tmp_path):
    return tmp_path / "repo"


async def test_reset_is_reproducible(repo):
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")

    env1 = SoftwareEnvironment(repo)
    obs1 = await env1.reset(task)

    env2 = SoftwareEnvironment(repo)
    obs2 = await env2.reset(task)

    assert obs1.file_tree == obs2.file_tree
    assert obs1.task == obs2.task


async def test_run_tests_reports_initial_failure(repo):
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")
    await env.reset(task)

    transition = await env.step(ToolAction(id="1", tool="run_tests", arguments={}))
    assert transition.current.tests_status == "failed"
    assert transition.current.failing_tests == 1


async def test_editing_source_fixes_and_verifies(repo):
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")
    await env.reset(task)

    await env.step(ToolAction(id="1", tool="run_tests", arguments={}))
    transition = await env.step(
        ToolAction(
            id="2",
            tool="edit_file",
            arguments={
                "path": "mathutils.py",
                "old_text": "return a + b + 1  # BUG: extra off-by-one term",
                "new_text": "return a + b",
            },
        )
    )
    assert transition.success
    assert transition.delta.files_touched == ["mathutils.py"]

    final = await env.step(ToolAction(id="3", tool="run_tests", arguments={}))
    assert final.current.tests_status == "passed"
    assert final.delta.fixed_tests == 1
    assert final.utility > 0


async def test_path_escape_is_rejected(repo):
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")
    await env.reset(task)

    transition = await env.step(ToolAction(id="1", tool="read_file", arguments={"path": "../../etc/passwd"}))
    assert not transition.success
    assert "escapes" in transition.error_message


async def test_stop_action_marks_done(repo):
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")
    await env.reset(task)

    transition = await env.step(ToolAction(id="1", tool="stop", arguments={}))
    assert transition.success
    assert env.is_done()


async def test_unrelated_tool_failure_does_not_pollute_recent_errors(repo):
    env = SoftwareEnvironment(repo)
    task = TaskSpec(id="t1", task="fix add()", fixture="toy_arithmetic_bug")
    await env.reset(task)

    transition = await env.step(ToolAction(id="1", tool="edit_file", arguments={"path": "nope.py", "content": "x"}))
    assert not transition.success
    assert transition.current.recent_errors == []


async def test_fastapi_postgres_fixture_materializes_with_its_five_defects(repo):
    """Plan §51: docker config, migration, DB connection, API route, Python bug."""
    from flycode.environment import fixtures

    result = fixtures.materialize("fastapi_postgres_app", repo)

    assert result.database_type == "postgres"
    assert result.docker_services == ["db", "app"]
    assert result.health_check_url == "http://localhost:8000/health"

    compose = (repo / "docker-compose.yml").read_text()
    assert '"8001:8000"' in compose  # docker configuration
    assert "script_location = alembic" in (repo / "alembic.ini").read_text()  # migration
    assert 'os.environ.get("POSTGRES_PASS"' in (repo / "app/database.py").read_text()  # DB connection

    main = (repo / "app/main.py").read_text()
    assert '@app.get("/user")' in main  # API route
    assert "select(User)" in main and "from sqlalchemy import select" not in main  # Python bug


async def test_compose_fixture_is_byte_identical_across_materials(tmp_path):
    from flycode.environment import fixtures

    first = fixtures.materialize("fastapi_postgres_app", tmp_path / "a")
    second = fixtures.materialize("fastapi_postgres_app", tmp_path / "b")

    def snapshot(root):
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    assert snapshot(first.path) == snapshot(second.path)


async def test_stale_infra_error_is_cleared_by_a_later_success(repo, monkeypatch):
    """A resolved infra failure must stop poisoning `is_healthy`.

    The Phase 4 fixture is broken on purpose, so the first probe of Postgres
    fails; a run could never satisfy the acceptance criterion if that first
    failure stuck in `recent_errors` forever.
    """
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="t1", task="run the app", fixture="fastapi_postgres_app"))

    monkeypatch.setattr(
        software_gym, "run_tool", lambda *a, **k: ToolResult(success=False, error_message="connection refused")
    )
    failed = await env.step(ToolAction(id="1", tool="postgres_status", arguments={}))
    assert failed.current.recent_errors == ["connection refused"]
    assert failed.current.is_healthy is False

    monkeypatch.setattr(
        software_gym, "run_tool", lambda *a, **k: ToolResult(success=True, payload={"connected": True})
    )
    recovered = await env.step(ToolAction(id="2", tool="postgres_status", arguments={}))
    assert recovered.current.recent_errors == []
    assert recovered.current.database["connected"] is True


async def test_failed_migration_is_reported_in_the_observation(repo):
    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="t1", task="run migrations", fixture="fastapi_postgres_app"))

    transition = await env.step(
        ToolAction(id="1", tool="alembic", arguments={"action": "upgrade", "revision": "head"})
    )

    assert not transition.success
    assert transition.current.database["migration_failed"] is True
    assert transition.current.recent_errors
    assert transition.current.is_healthy is False


async def test_passing_tests_do_not_complete_unverified_environment(repo, monkeypatch):
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    env = SoftwareEnvironment(repo)
    initial = await env.reset(TaskSpec(id="p4", task="run app", fixture="fastapi_postgres_app"))
    assert initial.docker["required"]
    monkeypatch.setattr(software_gym, "run_tool", lambda *a, **k: ToolResult(
        success=True, payload={"status": "passed", "passed": 2, "total": 2}
    ))
    passed = await env.step(ToolAction(id="test", tool="run_tests"))
    assert not passed.current.is_healthy


async def test_down_clears_running_state(repo, monkeypatch):
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="p4", task="run app", fixture="fastapi_postgres_app"))
    monkeypatch.setattr(software_gym, "run_tool", lambda *a, **k: ToolResult(success=True, payload={"running": False}))
    stopped = await env.step(ToolAction(id="down", tool="docker_compose", arguments={"action": "down"}))
    assert stopped.current.docker["running"] is False


async def test_edit_invalidates_previously_passing_tests(repo):
    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="toy", task="fix", fixture="toy_arithmetic_bug"))
    await env.step(ToolAction(id="fix", tool="edit_file", arguments={
        "path": "mathutils.py", "old_text": "a + b + 1", "new_text": "a + b",
    }))
    verified = await env.step(ToolAction(id="tests", tool="run_tests"))
    assert verified.current.is_healthy
    edited = await env.step(ToolAction(id="break", tool="edit_file", arguments={
        "path": "mathutils.py", "old_text": "a + b", "new_text": "a + b + 9",
    }))
    assert not edited.current.is_healthy
    assert edited.current.tests_status == "unknown"


async def test_service_readiness_clears_only_the_recovered_endpoint_error(repo, monkeypatch):
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="http", task="check endpoints", fixture="toy_arithmetic_bug"))
    monkeypatch.setattr(software_gym, "run_tool", lambda *a, **k: ToolResult(success=False, error_message=k["url"]))
    for url in ("http://localhost/a", "http://localhost/b"):
        await env.step(ToolAction(id=url, tool="http_request", arguments={"url": url}))
    monkeypatch.setattr(software_gym, "run_tool", lambda *a, **k: ToolResult(success=True))
    recovered = await env.step(ToolAction(id="a", tool="http_request", arguments={"url": "http://localhost/a"}))
    assert recovered.current.recent_errors == ["http://localhost/b"]
    assert not recovered.current.services["app_running"]


def test_migration_environment_reaches_database_configuration(repo, monkeypatch):
    import runpy

    import sqlalchemy
    from alembic import context
    from alembic.config import Config

    from flycode.environment import fixtures

    fixtures.materialize("fastapi_postgres_app", repo)
    ini = repo / "alembic.ini"
    ini.write_text(ini.read_text().replace("script_location = alembic", "script_location = migrations"))
    monkeypatch.setattr(context, "config", Config(str(ini)), raising=False)

    class DatabaseBoundary(Exception):
        pass

    def connect(*args, **kwargs):
        raise DatabaseBoundary

    monkeypatch.setattr(sqlalchemy, "engine_from_config", connect)
    with pytest.raises(DatabaseBoundary):
        runpy.run_path(str(repo / "migrations/env.py"))


async def test_restarting_stack_requires_fresh_verification(repo, monkeypatch):
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    env = SoftwareEnvironment(repo)
    await env.reset(TaskSpec(id="p4", task="run app", fixture="fastapi_postgres_app"))
    payloads = {
        "docker_compose": {"running": True},
        "postgres_status": {"connected": True},
        "alembic": {"migration_applied": True},
        "run_tests": {"status": "passed", "passed": 2},
    }
    monkeypatch.setattr(software_gym, "run_tool", lambda tool, *a, **k: ToolResult(
        success=True, payload=payloads.get(tool, {})
    ))
    for tool, arguments in [
        ("docker_compose", {"action": "up"}),
        ("postgres_status", {}),
        ("alembic", {"action": "upgrade"}),
        ("run_tests", {}),
        ("service_health", {"url": "http://localhost:8000/health"}),
        ("http_request", {"url": "http://localhost:8000/users"}),
    ]:
        await env.step(ToolAction(id=tool, tool=tool, arguments=arguments))
    assert (await env.observe()).is_healthy
    restarted = await env.step(ToolAction(id="restart", tool="docker_compose", arguments={"action": "restart"}))
    assert not restarted.current.is_healthy
    assert restarted.current.tests_status == "unknown"
    assert not restarted.current.services.get("checked_urls")
    assert not restarted.current.database.get("migration_applied")
    assert not restarted.current.database.get("connected")
