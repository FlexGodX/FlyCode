"""Opt-in Phase 5 acceptance against verified, external biological data."""

import os

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode

pytestmark = pytest.mark.skipif(os.environ.get('FLYCODE_TEST_CONNECTOME') != '1',
                                 reason='requires prepared FlyWire data and FLYCODE_TEST_CONNECTOME=1')


@pytest.mark.parametrize('fixture', ['toy_arithmetic_bug', 'toy_import_bug'])
@pytest.mark.parametrize('control', [ControlMode.BIAS_CONTROL, ControlMode.HARD_CONTROL])
async def test_real_flywire_runs_the_same_tasks(tmp_path, fixture, control):
    brain = make_brain('full', seed=0)
    assert len(brain.graph.neuron_ids) == 139255
    assert brain.graph.metadata['source'] == 'https://zenodo.org/records/10676866'
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), brain, HeuristicCandidateGenerator(),
                             TaskSpec(id='biological', task='fix the bug', fixture=fixture, max_steps=20),
                             control_mode=control)
    assert result.success, result.transcript
    assert brain.describe()['memory_usage_mb'] < 200
