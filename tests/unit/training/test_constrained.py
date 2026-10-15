"""Offline imitation changes only declared parameters, never adjacency."""

import json

import numpy as np
import pytest

from flycode.brain import make_stimulus
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.brain.full_connectome.sparse_backend import FullConnectomeBackend
from flycode.domain import Observation
from flycode.telemetry import TrainingStep


def graph_path(tmp_path):
    n = 64
    path = tmp_path / 'graph'
    graph_from_edges(np.arange(n), np.arange(n), np.roll(np.arange(n), 1), np.ones(n)).save(path)
    return path


def episodes():
    result = []
    for _ in range(3):
        result.append([
            TrainingStep(observation=Observation(task='fix', step_number=i,
                         tests={'status': 'passed' if i == 2 else 'failed', 'failed': int(i < 2)},
                         modified_files=['app.py'] if i else []).model_dump(),
                         chosen_action={'tool': tool}, outcome={'success': True})
            for i, tool in enumerate(['read_file', 'edit_file', 'stop'])
        ])
    return result


def test_training_improves_action_imitation_and_preserves_full_graph(tmp_path):
    from flycode.training.constrained import train_connectome

    path = graph_path(tmp_path)
    before = FullConnectomeBackend(connectome_path=path).graph.fingerprint
    report = train_connectome(path, episodes(), tmp_path / 'checkpoint', seed=0)
    assert report['trained']['tool_accuracy'] > report['frozen']['tool_accuracy']
    assert report['trained']['tool_accuracy'] == 1.
    assert report['trained']['mse'] < report['frozen']['mse']
    brain = FullConnectomeBackend(connectome_path=path, checkpoint_path=tmp_path / 'checkpoint')
    assert brain.graph.fingerprint == before == report['graph_fingerprint']
    expected = ['read_file', 'edit_file', 'stop']
    for step, tool in zip(episodes()[0], expected):
        state = brain.step(make_stimulus(Observation.model_validate(step.observation)))
        assert max(state.action_bias, key=state.action_bias.get) == tool
    brain.reset()
    first = brain.step(make_stimulus(Observation.model_validate(episodes()[0][0].observation)))
    assert max(first.action_bias, key=first.action_bias.get) == 'read_file'
    assert report['task_success_improvement'] is None  # imitation is not task completion
    json.dumps(report, allow_nan=False)


def test_checkpoint_rejects_different_topology_and_corrupt_parameters(tmp_path):
    from flycode.training.constrained import train_connectome

    path = graph_path(tmp_path)
    output = tmp_path / 'checkpoint'
    train_connectome(path, episodes(), output)
    other = tmp_path / 'other'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 2), np.ones(64)).save(other)
    with pytest.raises(ValueError, match='graph'):
        FullConnectomeBackend(connectome_path=other, checkpoint_path=output)
    with np.load(output / 'parameters.npz', allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    arrays['readout'][0, 0] = np.nan
    np.savez_compressed(output / 'parameters.npz', **arrays)
    with pytest.raises(ValueError, match='parameters'):
        FullConnectomeBackend(connectome_path=path, checkpoint_path=output)


def test_loader_keeps_resets_as_separate_episodes_and_validates_tool(tmp_path):
    from flycode.training.trajectories import load_episodes

    path = tmp_path / 'record.steps.jsonl'
    records = episodes()[0] + episodes()[0]
    path.write_text('\n'.join(step.model_dump_json() for step in records))
    assert [len(episode) for episode in load_episodes(tmp_path)] == [3, 3]
    records[0].chosen_action = {'tool': 'invented'}
    path.write_text('\n'.join(step.model_dump_json() for step in records))
    with pytest.raises(ValueError, match='tool'):
        load_episodes(tmp_path)


def test_no_empty_or_silent_training_on_missing_trajectories(tmp_path):
    from flycode.training.constrained import train_connectome
    from flycode.training.trajectories import load_episodes

    with pytest.raises(ValueError, match='trajector'):
        load_episodes(tmp_path)
    with pytest.raises(ValueError, match='episode'):
        train_connectome(graph_path(tmp_path), [], tmp_path / 'checkpoint')


def test_checkpoint_rejects_parameters_that_overflow_runtime_precision(tmp_path):
    import hashlib

    from flycode.training.constrained import train_connectome

    path = graph_path(tmp_path)
    output = tmp_path / 'checkpoint'
    train_connectome(path, episodes(), output)
    with np.load(output / 'parameters.npz', allow_pickle=False) as archive:
        arrays = {name: archive[name].astype(np.float64) for name in archive.files}
    arrays['readout'][0, 0] = 1e100
    np.savez_compressed(output / 'parameters.npz', **arrays)
    manifest = json.loads((output / 'checkpoint.json').read_text())
    manifest['sha256'] = hashlib.sha256((output / 'parameters.npz').read_bytes()).hexdigest()
    (output / 'checkpoint.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='parameters'):
        FullConnectomeBackend(connectome_path=path, checkpoint_path=output)
