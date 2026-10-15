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


async def test_run_metrics_are_populated(tmp_path):
    """Plan §66 metrics that used to be hardwired to 0 must be measured."""
    env = SoftwareEnvironment(tmp_path / "repo")
    brain = make_brain("functional", seed=0)
    task = TaskSpec(id="t1", task="fix the bug", fixture="toy_arithmetic_bug", max_steps=20)

    result = await run_agent(env, brain, HeuristicCandidateGenerator(), task)
    m = result.metrics

    assert result.success
    assert m.regressions == 0  # first run_tests on a broken repo is not a regression
    assert m.changed_lines > 0
    assert m.novel_observations > 0
    assert m.mean_candidate_entropy > 0.0
    assert brain.latency_ms > 0.0



async def test_successful_tool_diagnostics_are_visible_to_cortex(tmp_path, monkeypatch):
    from flycode.domain import CandidateAction
    from flycode.environment import software_gym
    from flycode.tools import ToolResult

    class DiagnosticCortex(HeuristicCandidateGenerator):
        def generate_candidates(self, context):
            if context.observation.step_number == 0:
                return [CandidateAction(id="logs", tool="docker_logs", arguments={"service": "app"})]
            assert any("missing relation users" in fact for fact in context.working_memory)
            assert any("stderr warning" in fact for fact in context.working_memory)
            return [CandidateAction(id="stop", tool="stop")]

    monkeypatch.setattr(software_gym, "run_tool", lambda *a, **k: ToolResult(
        success=True, stdout="missing relation users", stderr="stderr warning"
    ))
    result = await run_agent(SoftwareEnvironment(tmp_path), make_brain("functional"), DiagnosticCortex(),
                             TaskSpec(id="diag", task="diagnose", max_steps=2))
    assert not result.success


async def test_agent_rejects_stop_before_verification(tmp_path):
    from flycode.domain import CandidateAction

    class EarlyStopCortex(HeuristicCandidateGenerator):
        def generate_candidates(self, context):
            return [CandidateAction(id="stop", tool="stop")]

    result = await run_agent(SoftwareEnvironment(tmp_path), make_brain("functional"), EarlyStopCortex(),
                             TaskSpec(id="early", task="fix", max_steps=2))
    assert not result.success
    assert any("verification" in line for line in result.transcript)
