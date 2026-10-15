"""Connected induced subcircuits with inherited teacher mappings (plan §81)."""

import json
from pathlib import Path

import numpy as np

from .graph import SparseConnectome
from .sparse_backend import FullConnectomeBackend


def reduce_connectome(source: Path, output: Path, neurons: int, *, seed: int = 0,
                       checkpoint_path: Path | None = None) -> dict:
    teacher = FullConnectomeBackend(seed=seed, connectome_path=source, checkpoint_path=checkpoint_path)
    graph = teacher.graph
    n = len(graph.neuron_ids)
    if neurons < 2 or neurons >= n:
        raise ValueError('Reduced circuit must have at least two neurons and be smaller than its parent')
    if source.resolve() == output.resolve():
        raise ValueError('Reduction output must differ from parent graph directory')
    # Select a connected circuit using the existing topology, never add edges.
    adjacency = graph.weights.copy()
    adjacency.data = np.ones(adjacency.nnz, dtype=np.int8)
    adjacency = (adjacency + adjacency.T).tocsr()
    degree = np.diff(adjacency.indptr)
    queue = [int(np.argmax(degree))]
    visited = {queue[0]}
    cursor = 0
    while cursor < len(queue) and len(queue) < neurons:
        node = queue[cursor]
        cursor += 1
        for neighbor in adjacency.indices[adjacency.indptr[node]:adjacency.indptr[node + 1]]:
            neighbor = int(neighbor)
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
                if len(queue) == neurons:
                    break
    if len(queue) < neurons:
        raise ValueError('Selected connected component is smaller than requested circuit')
    indices = np.sort(np.asarray(queue))
    reduced = SparseConnectome(neuron_ids=graph.neuron_ids[indices].copy(),
                               weights=graph.weights[indices][:, indices].copy(), metadata={
                                   **graph.metadata, 'parent_fingerprint': graph.fingerprint,
                                   'parent_neurons': n, 'selection': 'BFS from highest undirected degree',
                                   'boundary_mapping': 'inherited from parent by original neuron ID',
                               })
    reduced.save(output)
    from ...training.checkpoint import save_parameters

    save_parameters(output / 'checkpoint', reduced.fingerprint, teacher._input[indices], teacher._readout,
                    teacher.dynamics, learned_tools=teacher._learned_tools,
                    metadata={'parent_fingerprint': graph.fingerprint})
    reduced_cost = reduced.weights.nnz * 3 + teacher._input[indices].size + teacher._readout.size
    report = {'parent_fingerprint': graph.fingerprint, 'fingerprint': reduced.fingerprint,
              'neurons': neurons, 'edges': reduced.weights.nnz, 'neuron_ratio': neurons / n,
              'estimated_cost_ratio': reduced_cost / teacher.get_computation_cost(),
              'selection': reduced.metadata['selection'], 'behavior_validation': None}
    (output / 'reduction.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    teacher.close()
    return report
