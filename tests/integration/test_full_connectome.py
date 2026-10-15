"""Phase 5 uses the same SoftwareGym/cortex/tool path as existing controllers."""

import numpy as np
import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode


@pytest.mark.parametrize('fixture', ['toy_arithmetic_bug', 'toy_import_bug'])
@pytest.mark.parametrize('control', [ControlMode.BIAS_CONTROL, ControlMode.HARD_CONTROL])
async def test_full_brain_repairs_the_same_toy_tasks(tmp_path, fixture, control):
    path = tmp_path / 'graph'
    n = 64
    graph_from_edges(np.arange(n), np.arange(n), np.roll(np.arange(n), 1), np.ones(n)).save(path)
    brain = make_brain('full', connectome_path=path, seed=0)
    events = []
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), brain, HeuristicCandidateGenerator(),
                             TaskSpec(id='full', task='fix', fixture=fixture, max_steps=15),
                             control_mode=control, on_event=lambda kind, data: events.append((kind, data)))
    assert result.success, result.transcript
    assert any(data['state']['connectome']['neurons'] == n for kind, data in events if kind == 'controller')
    assert result.metrics.brain == 'full_connectome'
