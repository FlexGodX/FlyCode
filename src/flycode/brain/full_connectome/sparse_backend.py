"""Frozen biological adjacency with modeled rate dynamics (plan §36,79).

Input/readout mappings are seeded engineering choices. Connectivity and
synapse counts come from the prepared dataset; this is not a biophysical
simulation or evidence that a biological brain understands software.
"""

import os
import time
from pathlib import Path

import numpy as np
from pydantic import Field

from ...domain import VALID_TOOLS, NeuralState, NeuralStimulus
from ..functional_fly import ALL_MODES, MODE_TOOL_TABLE
from ..protocol import BrainBackend
from ..random_reservoir import _softmax, observation_features
from .graph import SparseConnectome

DEFAULT_CONNECTOME = Path.home() / '.cache/flycode/flywire-783/prepared'


class ConnectomeState(NeuralState):
    connectome: dict = Field(default_factory=dict)


class FullConnectomeBackend(BrainBackend):
    name = 'full_connectome'
    tier = 3

    def __init__(self, seed: int | None = None, connectome_path: str | Path | None = None) -> None:
        super().__init__(seed=seed)
        path = Path(connectome_path or os.environ.get('FLYCODE_CONNECTOME_PATH') or DEFAULT_CONNECTOME)
        self.graph = SparseConnectome.load(path)
        self._topology_hash = self.graph.topology_hash
        n = len(self.graph.neuron_ids)
        rng = np.random.default_rng(seed if seed is not None else 0)
        self._input = rng.normal(0, .25, (n, 13)).astype(np.float32)
        self._groups = (self.graph.neuron_ids % 64).astype(np.int32)
        self._group_counts = np.maximum(np.bincount(self._groups, minlength=64), 1)
        self._readout = rng.normal(0, .15, (len(ALL_MODES) + len(VALID_TOOLS), 64)).astype(np.float32)
        self.reset()

    def reset(self) -> None:
        self._activity = np.zeros(len(self.graph.neuron_ids), dtype=np.float32)
        self._state = NeuralState()
        self._steps = 0
        self.latency_ms = 0.
        self.memory_usage_mb = self._memory_usage()

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        start = time.perf_counter()
        obs = stimulus.observation
        x = np.append(observation_features(stimulus), 1.).astype(np.float32)
        drive = self._input @ x * stimulus.intensity
        for _ in range(3):
            self._activity = (.7 * self._activity + .3 * np.tanh(
                .9 * (self.graph.weights @ self._activity) + drive)).astype(np.float32)
        pooled = np.bincount(self._groups, weights=self._activity, minlength=64) / self._group_counts
        output = np.tanh(self._readout @ pooled)
        # Hand-initialized workflow prior bootstraps the untrained boundary
        # readout. The actual neural readout contributes to every tool score.
        prior_mode = ('consolidate' if obs.is_healthy else 'verify' if obs.tests_status == 'unknown' else 'execute')
        mode_scores = output[:len(ALL_MODES)].copy()
        mode_scores[ALL_MODES.index(prior_mode)] += 3.
        probs = _softmax(mode_scores)
        mode = ALL_MODES[int(np.argmax(probs))]
        tool_scores = output[len(ALL_MODES):]
        prior = MODE_TOOL_TABLE[mode]
        bias = {tool: float(np.clip(prior.get(tool, 0.) + .25 * value, -1., 1.))
                for tool, value in zip(VALID_TOOLS, tool_scores)}
        self._steps += 1
        self.latency_ms = (time.perf_counter() - start) * 1000
        self._state = ConnectomeState(
            state_vector=pooled.tolist(), task_direction=probs.tolist(),
            mode_probs=dict(zip(ALL_MODES, probs.tolist())), behavior_mode=mode,
            action_bias=bias, confidence=float(np.max(probs)),
            risk_level=min(1., len(obs.recent_errors) / 5.),
            novelty=float(np.clip(np.std(self._activity), 0., 1.)),
            valence=float(np.tanh(np.mean(self._activity))),
            connectome={'neurons': len(self.graph.neuron_ids), 'edges': self.graph.weights.nnz,
                        'topology_hash': self._topology_hash,
                        'source': self.graph.metadata.get('source'), 'release': self.graph.metadata.get('release')},
        )
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def activity(self) -> np.ndarray:
        return self._activity.copy()

    def close(self) -> None:
        self._activity = np.zeros(0, dtype=np.float32)
        self.memory_usage_mb = self._memory_usage()

    def set_learning_mode(self, enabled: bool) -> None:
        if enabled:
            raise ValueError('Frozen connectome learning requires an explicit offline training workflow')
        super().set_learning_mode(False)

    def _memory_usage(self) -> float:
        matrix = self.graph.weights
        arrays = (matrix.data, matrix.indices, matrix.indptr, self.graph.neuron_ids,
                  self._input, self._readout, self._activity, self._groups, self._group_counts)
        return sum(array.nbytes for array in arrays) / 1024 ** 2

    def parameter_count(self) -> int:
        return int(self.graph.weights.nnz + self._input.size + self._readout.size)

    def get_memory_capacity(self) -> int:
        return len(self.graph.neuron_ids)

    def get_computation_cost(self) -> float:
        return float(self.graph.weights.nnz * 3 + self._input.size + self._readout.size)

    def describe(self) -> dict:
        return {**super().describe(), 'state_dim': len(self.graph.neuron_ids),
                'telemetry_dim': 64, 'edges': self.graph.weights.nnz,
                'memory_usage_mb': self.memory_usage_mb, 'source': self.graph.metadata,
                'dynamics': '3 leaky tanh substeps; gain=.9, leak=.3',
                'boundary_mapping': 'seeded input/readout with explicit workflow prior'}
