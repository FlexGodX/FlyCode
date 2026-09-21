"""Integration test: the full online agent loop fixes deterministic toy repos.

This is the Phase 1 acceptance criterion (plan §75): "agent fixes
deterministic toy repositories", exercised end-to-end through
`run_agent` (LLM cortex -> brain bias -> safety mask -> SoftwareGym) rather
than any single component in isolation.
"""

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode


@pytest.mark.parametrize("brain_name", ["functional", "heuristic_fsm"])
@pytest.mark.parametrize("fixture", ["toy_arithmetic_bug", "toy_import_bug"])
async def test_agent_fixes_toy_repo(tmp_path, brain_name, fixture):
    env = SoftwareEnvironment(tmp_path / "repo")
    brain = make_brain(brain_name, seed=0)
    cortex = HeuristicCandidateGenerator()
    task = TaskSpec(id="t1", task="fix the bug", fixture=fixture, max_steps=20)

    result = await run_agent(env, brain, cortex, task, control_mode=ControlMode.BIAS_CONTROL)

    assert result.success
    assert result.metrics.verified_completion
    assert result.episode.final_result


async def test_agent_run_is_reproducible(tmp_path):
    task = TaskSpec(id="t1", task="fix the bug", fixture="toy_arithmetic_bug", max_steps=20)

    env1 = SoftwareEnvironment(tmp_path / "repo1")
    result1 = await run_agent(env1, make_brain("functional", seed=7), HeuristicCandidateGenerator(), task)

    env2 = SoftwareEnvironment(tmp_path / "repo2")
    result2 = await run_agent(env2, make_brain("functional", seed=7), HeuristicCandidateGenerator(), task)

    assert result1.success and result2.success
    assert result1.steps == result2.steps
    assert result1.transcript == result2.transcript


async def test_hard_control_also_solves_the_task(tmp_path):
    env = SoftwareEnvironment(tmp_path / "repo")
    task = TaskSpec(id="t1", task="fix the bug", fixture="toy_arithmetic_bug", max_steps=20)

    result = await run_agent(
        env,
        make_brain("functional", seed=0),
        HeuristicCandidateGenerator(),
        task,
        control_mode=ControlMode.HARD_CONTROL,
    )
    assert result.success


async def test_resource_constrained_task_stops_within_budget(tmp_path):
    env = SoftwareEnvironment(tmp_path / "repo")
    task = TaskSpec(id="t1", task="fix the bug", fixture="toy_arithmetic_bug", max_steps=50, max_tool_calls=3)

    result = await run_agent(env, make_brain("functional", seed=0), HeuristicCandidateGenerator(), task)
    assert result.tool_calls <= 3
