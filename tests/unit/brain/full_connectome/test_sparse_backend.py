"""Real sparse recurrence drives a compact, JSON-compatible agent readout."""

import json

import numpy as np
import pytest

from flycode.brain import make_stimulus
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.brain.full_connectome.sparse_backend import FullConnectomeBackend
from flycode.domain import VALID_TOOLS, Observation


def artifact(path, reverse=False):
    graph_from_edges(np.array([1, 2, 3]), np.array([2, 3] if reverse else [1, 2]),
                     np.array([1, 2] if reverse else [2, 3]), np.array([3, 4])).save(path)
    return path


def test_topology_changes_state_and_real_tool_preferences(tmp_path):
    a = FullConnectomeBackend(connectome_path=artifact(tmp_path / 'a'), seed=0)
    b = FullConnectomeBackend(connectome_path=artifact(tmp_path / 'b', True), seed=0)
    stimulus = make_stimulus(Observation(task='fix', tests={'status': 'failed', 'failed': 1}))
    left, right = a.step(stimulus), b.step(stimulus)
    assert left.state_vector != right.state_vector
    assert left.action_bias != right.action_bias
    assert set(left.action_bias) == set(VALID_TOOLS)
    json.dumps(left.model_dump())
    assert left.connectome['neurons'] == 3


def test_recurrence_is_persistent_reset_is_reproducible_and_graph_is_frozen(tmp_path):
    brain = FullConnectomeBackend(connectome_path=artifact(tmp_path / 'graph'), seed=4)
    stimulus = make_stimulus(Observation(task='fix'))
    before = brain.graph.fingerprint
    first = brain.step(stimulus).state_vector
    second = brain.step(stimulus).state_vector
    assert first != second
    brain.reset()
    assert brain.step(stimulus).state_vector == pytest.approx(first)
    assert brain.graph.fingerprint == before
    assert not brain.graph.weights.data.flags.writeable
    with pytest.raises(ValueError):
        brain.graph.weights.data[0] = 0


def test_missing_graph_never_falls_back_to_random_projection(tmp_path):
    with pytest.raises(ValueError, match='connectome'):
        FullConnectomeBackend(connectome_path=tmp_path / 'missing')


def test_telemetry_width_is_bounded_by_readout_not_neuron_count(tmp_path):
    n = 400
    path = tmp_path / 'graph'
    graph_from_edges(np.arange(n), np.arange(n - 1), np.arange(1, n), np.ones(n - 1)).save(path)
    brain = FullConnectomeBackend(connectome_path=path, seed=0)
    state = brain.step(make_stimulus(Observation(task='fix')))
    assert len(state.state_vector) <= 64
    assert brain.activity().shape == (n,)
    assert brain.describe()['state_dim'] == n
    assert brain.describe()['memory_usage_mb'] > 0
