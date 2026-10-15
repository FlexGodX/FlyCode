"""Reduction retains measured edges and the teacher's boundary mappings."""

import numpy as np
import pytest
from scipy.sparse.csgraph import connected_components

from flycode.brain import make_brain, make_stimulus
from flycode.brain.full_connectome.graph import SparseConnectome, graph_from_edges
from flycode.domain import Observation


def parent_graph(tmp_path):
    path = tmp_path / 'parent'
    n = 128
    graph_from_edges(np.arange(n), np.arange(n), np.roll(np.arange(n), 1), np.ones(n),
                     metadata={'source': 'explicit test ring'}).save(path)
    return path


def test_reduction_is_connected_induced_and_retains_teacher_mapping(tmp_path):
    from flycode.brain.full_connectome.reduction import reduce_connectome

    parent = parent_graph(tmp_path)
    teacher = make_brain('full', connectome_path=parent, seed=3)
    output = tmp_path / 'reduced'
    report = reduce_connectome(parent, output, 16, seed=3)
    student = make_brain('reduced', connectome_path=output)
    indices = np.searchsorted(teacher.graph.neuron_ids, student.graph.neuron_ids)
    expected = teacher.graph.weights[indices][:, indices]
    assert (expected != student.graph.weights).nnz == 0
    assert connected_components(student.graph.weights, directed=False)[0] == 1
    np.testing.assert_array_equal(student._input, teacher._input[indices])
    assert student.graph.metadata['parent_fingerprint'] == teacher.graph.fingerprint
    assert student.graph.metadata['source'] == 'explicit test ring'
    assert report['neuron_ratio'] == .125
    # The unchanged 64-column readout dominates this deliberately tiny graph.
    assert report['estimated_cost_ratio'] < .6
    first = student.step(make_stimulus(Observation(task='fix'))).model_dump()
    reloaded = make_brain('reduced', connectome_path=output)
    assert reloaded.step(make_stimulus(Observation(task='fix'))).model_dump() == first
    assert student.tier == 2


def test_reduction_does_not_modify_parent_and_rejects_invalid_sizes(tmp_path):
    from flycode.brain.full_connectome.reduction import reduce_connectome

    path = parent_graph(tmp_path)
    before = SparseConnectome.load(path).fingerprint
    with pytest.raises(ValueError, match='smaller'):
        reduce_connectome(path, tmp_path / 'invalid', 128)
    with pytest.raises(ValueError, match='smaller'):
        reduce_connectome(path, tmp_path / 'invalid', 1)
    reduce_connectome(path, tmp_path / 'reduced', 12)
    assert SparseConnectome.load(path).fingerprint == before
    with pytest.raises(ValueError, match='reduced'):
        make_brain('reduced', connectome_path=path)
