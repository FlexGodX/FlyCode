"""Directed sparse connectome artifacts with explicit provenance (plan §36–39)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import sparse


class SparseConnectome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    neuron_ids: np.ndarray
    weights: Any  # CSR: target rows, source columns; normalized absolute row sum <= 1.
    metadata: dict = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for array in (self.neuron_ids, self.weights.indptr, self.weights.indices, self.weights.data):
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()

    @property
    def topology_hash(self) -> str:
        digest = hashlib.sha256()
        for array in (self.neuron_ids, self.weights.indptr, self.weights.indices):
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        sparse.save_npz(directory / 'weights.npz', self.weights)
        np.save(directory / 'neuron_ids.npy', self.neuron_ids, allow_pickle=False)
        manifest = {**self.metadata, 'format_version': 1, 'neurons': len(self.neuron_ids),
                    'edges': self.weights.nnz, 'fingerprint': self.fingerprint,
                    'topology_hash': self.topology_hash}
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    @classmethod
    def load(cls, directory: Path) -> 'SparseConnectome':
        try:
            manifest = json.loads((directory / 'manifest.json').read_text())
            ids = np.load(directory / 'neuron_ids.npy', allow_pickle=False)
            weights = sparse.load_npz(directory / 'weights.npz').tocsr()
        except (OSError, ValueError) as exc:
            raise ValueError(f'Cannot load connectome at {directory}: {exc}') from exc
        if manifest.get('format_version') != 1:
            raise ValueError('Unsupported connectome format')
        if ids.ndim != 1 or ids.dtype.kind not in 'ui' or len(ids) == 0 or np.any(ids[1:] <= ids[:-1]):
            raise ValueError('Neuron IDs must be sorted unique integers')
        if weights.shape != (len(ids), len(ids)) or weights.nnz == 0:
            raise ValueError('Invalid connectome matrix shape or empty topology')
        if not np.all(np.isfinite(weights.data)) or np.any(np.abs(weights).sum(axis=1).A1 > 1.00001):
            raise ValueError('Connectome weights must be finite and row-normalized')
        graph = cls(neuron_ids=ids, weights=weights, metadata=manifest)
        if graph.fingerprint != manifest.get('fingerprint') or graph.topology_hash != manifest.get('topology_hash'):
            raise ValueError('Connectome fingerprint mismatch')
        for array in (graph.neuron_ids, weights.data, weights.indices, weights.indptr):
            array.flags.writeable = False
        return graph


def graph_from_edges(neuron_ids: np.ndarray, pre: np.ndarray, post: np.ndarray,
                     counts: np.ndarray, *, min_synapses: int = 1,
                     source_signs: np.ndarray | None = None, metadata: dict | None = None) -> SparseConnectome:
    """Aggregate measured synapse counts before filtering and normalization."""
    if min_synapses < 1 or neuron_ids.ndim != 1 or neuron_ids.dtype.kind not in 'ui' or not len(neuron_ids):
        raise ValueError('Expected nonempty integer neuron IDs and a positive synapse threshold')
    ids = np.sort(neuron_ids.astype(np.uint64))
    if len(np.unique(ids)) != len(ids) or np.any(neuron_ids < 0):
        raise ValueError('Neuron IDs must be unique and nonnegative')
    if pre.dtype.kind not in 'ui' or post.dtype.kind not in 'ui':
        raise ValueError('Edge endpoints must be integer neuron IDs')
    if np.any(pre < 0) or np.any(post < 0):
        raise ValueError('Edge endpoints must be nonnegative')
    pre, post = pre.astype(np.uint64), post.astype(np.uint64)
    if pre.ndim != 1 or post.shape != pre.shape or counts.shape != pre.shape:
        raise ValueError('Edge columns must have matching one-dimensional shapes')
    if not np.all(np.isfinite(counts)) or np.any(counts <= 0):
        raise ValueError('Synapse counts must be finite and positive')
    sources, targets = np.searchsorted(ids, pre), np.searchsorted(ids, post)
    if (np.any(sources >= len(ids)) or np.any(targets >= len(ids))
            or np.any(ids[np.minimum(sources, len(ids) - 1)] != pre)
            or np.any(ids[np.minimum(targets, len(ids) - 1)] != post)):
        raise ValueError('Edge endpoint is absent from the neuron table')
    weights = sparse.coo_matrix((counts.astype(np.float32), (targets, sources)), shape=(len(ids), len(ids))).tocsr()
    weights.sum_duplicates()
    weights.data[weights.data < min_synapses] = 0
    weights.eliminate_zeros()
    if not weights.nnz:
        raise ValueError('No connections remain after synapse thresholding')
    if source_signs is not None:
        if source_signs.shape != ids.shape or not np.all(np.isin(source_signs, [-1, 1])):
            raise ValueError('Expected one excitatory/inhibitory sign per sorted neuron')
        weights.data *= source_signs[weights.indices]
    row_norm = np.abs(weights).sum(axis=1).A1
    weights.data /= np.repeat(np.maximum(row_norm, 1), np.diff(weights.indptr))
    weights.sort_indices()
    return SparseConnectome(neuron_ids=ids, weights=weights, metadata={
        'source': 'user-supplied edge table', 'min_synapses': min_synapses,
        'normalization': 'absolute incoming row sum', **(metadata or {}),
    })
