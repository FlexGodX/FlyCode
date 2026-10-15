"""Fixed-topology lesion/sensitivity probes on recorded trajectories (§67–68).

These replay measurements do not estimate counterfactual task success.
"""

import hashlib
import time
from pathlib import Path

import numpy as np

from ..brain.full_connectome.sparse_backend import FullConnectomeBackend
from ..telemetry import TrainingStep
from ..training.checkpoint import Dynamics
from ..training.constrained import replay
from ..training.trajectories import fingerprint_episodes


def _check_episodes(episodes: list[list[TrainingStep]]) -> None:
    if not episodes or any(not episode for episode in episodes):
        raise ValueError('Perturbation probes require nonempty recorded episodes')


def _difference(states: np.ndarray, scores: np.ndarray, baseline: tuple[np.ndarray, np.ndarray]) -> dict:
    return {'state_rmse': float(np.sqrt(np.mean((states - baseline[0]) ** 2))),
            'bias_rmse': float(np.sqrt(np.mean((scores - baseline[1]) ** 2))),
            'tool_agreement': float(np.mean(np.argmax(scores, axis=1) == np.argmax(baseline[1], axis=1)))}


def apply_lesion(brain: FullConnectomeBackend, fraction: float, seed: int) -> dict:
    """Apply one reproducible mask to an isolated controller, never its source artifact."""
    if not np.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('Lesion fraction must be finite and between zero and one')
    parent = brain.graph.fingerprint
    n = len(brain.graph.neuron_ids)
    selected = np.sort(np.random.default_rng(seed).choice(n, size=round(n * fraction), replace=False))
    mask = np.zeros(n, dtype=bool)
    mask[selected] = True
    weights = brain.graph.weights.copy()
    # Zero gain on existing edges, retaining CSR structure and topology hash.
    weights.data[np.repeat(mask, np.diff(weights.indptr)) | mask[weights.indices]] = 0.
    brain.graph = brain.graph.model_copy(update={'weights': weights})
    brain._input[selected] = 0.
    return {'seed': seed, 'fraction': fraction, 'neurons_lesioned': len(selected),
            'neuron_ids_sha256': hashlib.sha256(brain.graph.neuron_ids[selected].tobytes()).hexdigest(),
            'parent_fingerprint': parent, 'graph_fingerprint': brain.graph.fingerprint,
            'topology_hash': brain.graph.topology_hash,
            'active_edges': int(np.count_nonzero(weights.data)), 'structural_edges': weights.nnz}


def lesion(graph_path: Path, episodes: list[list[TrainingStep]], *, fraction: float = .1, seed: int = 0,
           checkpoint_path: Path | None = None) -> dict:
    _check_episodes(episodes)
    baseline = FullConnectomeBackend(seed=seed, connectome_path=graph_path, checkpoint_path=checkpoint_path)
    reference = replay(baseline, episodes)
    damaged = FullConnectomeBackend(seed=seed, connectome_path=graph_path, checkpoint_path=checkpoint_path)
    perturbation = apply_lesion(damaged, fraction, seed)
    start = time.perf_counter()
    states, scores = replay(damaged, episodes)
    report = {'experiment': 'zero input and incident synaptic gains for sampled neurons',
              **perturbation,
              'dataset_sha256': fingerprint_episodes(episodes), 'state_space': '64 pooled neural values',
              'counterfactual_task_success': None, 'replay_wall_time_ms': (time.perf_counter() - start) * 1000,
              **_difference(states, scores, reference)}
    baseline.close()
    damaged.close()
    return report


def sensitivity(graph_path: Path, episodes: list[list[TrainingStep]], *, parameter: str,
                values: list[float], seed: int = 0, checkpoint_path: Path | None = None) -> dict:
    _check_episodes(episodes)
    if parameter not in Dynamics.model_fields or not values:
        raise ValueError('Specify a dynamics parameter and at least one value')
    baseline = FullConnectomeBackend(seed=seed, connectome_path=graph_path, checkpoint_path=checkpoint_path)
    configurations = [Dynamics.model_validate({**baseline.dynamics.model_dump(), parameter: value}) for value in values]
    reference = replay(baseline, episodes)
    trials = []
    for value, dynamics in zip(values, configurations):
        brain = FullConnectomeBackend(seed=seed, connectome_path=graph_path, checkpoint_path=checkpoint_path)
        brain.dynamics = dynamics
        start = time.perf_counter()
        states, scores = replay(brain, episodes)
        trials.append({'value': value, 'topology_hash': brain.graph.topology_hash,
                       'replay_wall_time_ms': (time.perf_counter() - start) * 1000,
                       **_difference(states, scores, reference)})
        brain.close()
    report = {'parameter': parameter, 'seed': seed, 'baseline_dynamics': baseline.dynamics.model_dump(),
              'graph_fingerprint': baseline.graph.fingerprint, 'dataset_sha256': fingerprint_episodes(episodes),
              'counterfactual_task_success': None, 'state_space': '64 pooled neural values', 'trials': trials}
    baseline.close()
    return report
