"""Opt-in acceptance against the user's installed LM Studio models."""

import os

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import make_lmstudio_cortex
from flycode.telemetry import TelemetryLogger

pytestmark = pytest.mark.skipif(
    os.environ.get("FLYCODE_TEST_LMSTUDIO") != "1",
    reason="set FLYCODE_TEST_LMSTUDIO=1 to run both installed local models",
)


async def test_installed_qwen_and_jev_repair_a_toy_repository(tmp_path):
    cortex = make_lmstudio_cortex(max_model_calls=30)
    logger = TelemetryLogger(tmp_path / "telemetry")
    result = await run_agent(
        SoftwareEnvironment(tmp_path / "repo"), make_brain("functional", seed=0), cortex,
        TaskSpec(id="local-live", task="Fix the off-by-one bug in add and verify the tests",
                 fixture="toy_arithmetic_bug", max_steps=20), telemetry=logger,
    )
    assert result.success, "\n".join(result.transcript)
    assert result.metrics.model_calls["qwen/qwen3.5-9b"] > 0
    assert result.metrics.model_calls["jev-style-qwen3.5-2b-decision"] > 0
    assert result.metrics.llm_calls == sum(result.metrics.model_calls.values())
    assert result.metrics.llm_calls <= 30
    assert any(step.model_routing["decision_scores"] for step in logger.read_steps("run"))
    assert any(step.model_routing["route"] == "local" for step in logger.read_steps("run"))
