"""Record actual executed repairs, train offline, reload into the same agent."""

import numpy as np

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.telemetry import TelemetryLogger


async def test_trained_checkpoint_repairs_tasks_from_recorded_episodes(tmp_path):
    from flycode.training.constrained import train_connectome
    from flycode.training.trajectories import load_episodes

    path = tmp_path / 'graph'
    n = 128
    graph_from_edges(np.arange(n), np.arange(n), np.roll(np.arange(n), 1), np.ones(n)).save(path)
    telemetry = TelemetryLogger(tmp_path / 'trajectories')
    fixtures = ['toy_arithmetic_bug', 'toy_import_bug']
    for fixture in fixtures:
        result = await run_agent(SoftwareEnvironment(tmp_path / fixture), make_brain('functional', seed=0),
                                 HeuristicCandidateGenerator(), TaskSpec(id=fixture, task='fix', fixture=fixture),
                                 telemetry=telemetry, run_id=fixture)
        assert result.success
    episodes = load_episodes(telemetry.directory)
    report = train_connectome(path, episodes, tmp_path / 'trained')
    assert report['trained']['mse'] < report['frozen']['mse']
    for fixture in fixtures:
        result = await run_agent(SoftwareEnvironment(tmp_path / ('trained-' + fixture)),
                                 make_brain('full', connectome_path=path, checkpoint_path=tmp_path / 'trained'),
                                 HeuristicCandidateGenerator(), TaskSpec(id=fixture, task='fix', fixture=fixture))
        assert result.success, result.transcript
        assert result.metrics.verified_completion
