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
