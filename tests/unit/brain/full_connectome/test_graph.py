"""Biological edge direction and IDs are data contracts, not random projections."""

import numpy as np
import pytest
from scipy import sparse

from flycode.brain.full_connectome.graph import SparseConnectome, graph_from_edges


def test_graph_sums_neuropils_before_thresholding_and_preserves_large_ids():
    root = 720575940600000001
    graph = graph_from_edges(
        np.array([root + 2, root, root + 1], dtype=np.uint64),
        np.array([root, root, root + 1], dtype=np.uint64),
        np.array([root + 1, root + 1, root + 2], dtype=np.uint64),
        np.array([2, 3, 7]), min_synapses=5,
    )
    assert graph.neuron_ids.tolist() == [root, root + 1, root + 2]
    assert graph.weights.nnz == 2
    assert graph.weights[1, 0] == 1
    assert graph.weights[2, 1] == 1
    assert graph.weights[0, 1] == 0


@pytest.mark.parametrize('pre,post,counts', [
    ([1], [9], [1]), ([1], [2], [float('nan')]), ([1], [2], [-1]),
])
def test_invalid_edges_are_rejected(pre, post, counts):
    with pytest.raises(ValueError):
        graph_from_edges(np.array([1, 2]), np.array(pre), np.array(post), np.array(counts))


def test_graph_round_trip_checks_fingerprint_and_remains_sparse(tmp_path):
    graph = graph_from_edges(np.array([1, 2, 3]), np.array([1, 2]), np.array([2, 3]), np.array([3, 4]))
    graph.save(tmp_path)
    loaded = SparseConnectome.load(tmp_path)
    assert sparse.issparse(loaded.weights)
    assert loaded.fingerprint == graph.fingerprint
    assert loaded.neuron_ids.tolist() == [1, 2, 3]
    changed = sparse.load_npz(tmp_path / 'weights.npz')
    changed.data[0] = .25
    sparse.save_npz(tmp_path / 'weights.npz', changed)
    with pytest.raises(ValueError, match='fingerprint'):
        SparseConnectome.load(tmp_path)


def test_signed_integer_ids_do_not_round_nearby_large_neurons():
    root = 720575940600000001
    graph = graph_from_edges(np.array([root, root + 1, root + 2], dtype=np.int64),
                             np.array([root + 1], dtype=np.int64),
                             np.array([root + 2], dtype=np.int64), np.array([1]))
    assert graph.weights[2, 1] == 1
    assert graph.weights[0, 0] == 0
