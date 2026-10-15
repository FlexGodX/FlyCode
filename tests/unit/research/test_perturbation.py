"""Causal probes alter only the stated parameter or lesion mask."""

import numpy as np
import pytest

from flycode.brain.full_connectome.graph import SparseConnectome, graph_from_edges
from flycode.domain import Observation
from flycode.telemetry import TrainingStep


def inputs(tmp_path):
    path = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(path)
    episodes = [[TrainingStep(observation=Observation(task='probe', step_number=i).model_dump(),
                               chosen_action={'tool': 'read_file'}, outcome={}) for i in range(4)]]
    return path, episodes


def test_full_lesion_changes_neural_activity_without_mutating_parent(tmp_path):
    from flycode.research.perturbation import lesion

    path, episodes = inputs(tmp_path)
    before = SparseConnectome.load(path).fingerprint
    zero = lesion(path, episodes, fraction=0., seed=0)
    damaged = lesion(path, episodes, fraction=1., seed=0)
    assert zero['state_rmse'] == 0.
    assert zero['bias_rmse'] == 0.
    assert damaged['state_rmse'] > .01
    assert damaged['bias_rmse'] > 0.
    assert damaged['active_edges'] == 0
    assert damaged['topology_hash'] == zero['topology_hash']
    assert SparseConnectome.load(path).fingerprint == before
    assert damaged['counterfactual_task_success'] is None


def test_sensitivity_replays_from_identical_initialization_and_validates_limits(tmp_path):
    from flycode.research.perturbation import sensitivity

    path, episodes = inputs(tmp_path)
    report = sensitivity(path, episodes, parameter='recurrent_gain', values=[.9, .3], seed=2)
    assert report['trials'][0]['state_rmse'] == 0.
    assert report['trials'][1]['state_rmse'] > 0.
    assert len({trial['topology_hash'] for trial in report['trials']}) == 1
    with pytest.raises(ValueError):
        sensitivity(path, episodes, parameter='leak', values=[np.nan])
    with pytest.raises(ValueError):
        sensitivity(path, episodes, parameter='leak', values=[0.])
