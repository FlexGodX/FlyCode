"""Reduced biological circuit; never substitutes a synthetic reservoir (§81)."""

import os
from pathlib import Path

from .full_connectome.sparse_backend import FullConnectomeBackend


class ReducedConnectomeBackend(FullConnectomeBackend):
    name = 'reduced_connectome'
    tier = 2

    def __init__(self, seed: int | None = None, connectome_path: str | Path | None = None,
                 checkpoint_path: str | Path | None = None) -> None:
        path = connectome_path or os.environ.get('FLYCODE_REDUCED_PATH')
        if path is None:
            raise ValueError('A prepared reduced connectome path is required')
        path = Path(path)
        # Validate provenance before loading the associated boundary checkpoint.
        from .full_connectome.graph import SparseConnectome

        graph = SparseConnectome.load(path)
        if not graph.metadata.get('parent_fingerprint'):
            raise ValueError('This graph is not a prepared reduced connectome')
        super().__init__(seed=seed, connectome_path=path, checkpoint_path=checkpoint_path or path / 'checkpoint')
