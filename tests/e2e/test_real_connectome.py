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


async def test_constrained_training_improves_explicit_multicandidate_task_selection(tmp_path):
    from flycode.brain.full_connectome.sparse_backend import DEFAULT_CONNECTOME
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.training.constrained import train_connectome
    from flycode.training.trajectories import load_episodes

    teacher = await run_benchmark(BenchmarkConfig.model_validate({
        'controllers': [{'id': 'teacher', 'brain': 'full'}], 'seeds': [0],
        'controls': ['bias'], 'cortex': 'tournament', 'max_steps': 15,
    }), tmp_path / 'teacher')
    assert teacher['summary']['teacher']['success_rate'] == 1.
    episodes = load_episodes(tmp_path / 'teacher' / teacher['experiment_id'] / 'trajectories')
    checkpoint = tmp_path / 'trained'
    training = train_connectome(DEFAULT_CONNECTOME, episodes, checkpoint, seed=0)
    compared = await run_benchmark(BenchmarkConfig.model_validate({
        'controllers': [{'id': 'frozen', 'brain': 'full'},
                        {'id': 'trained', 'brain': 'full', 'checkpoint': str(checkpoint)}],
        'seeds': [0], 'controls': ['hard'], 'cortex': 'tournament', 'max_steps': 15,
    }), tmp_path / 'comparison')
    assert compared['summary']['trained']['success_rate'] == 1., compared['summary']
    assert compared['summary']['trained']['success_rate'] > compared['summary']['frozen']['success_rate']
    assert all(case['graph_fingerprint'] == training['graph_fingerprint'] for case in compared['cases'])
