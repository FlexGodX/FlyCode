"""Offline imitation with a frozen graph and selected bounded dynamics (§37,54).

A small grid searches recurrent gain and leak; ridge regression fits tool
readout on recorded decisions. Reports distinguish in-sample imitation from
actual SoftwareGym completion. Neither metric is a claim of generalization.
"""

import json
from pathlib import Path

import numpy as np

from ..brain import make_stimulus
from ..brain.full_connectome.sparse_backend import FullConnectomeBackend
from ..brain.functional_fly import ALL_MODES
from ..domain import VALID_TOOLS, Observation
from ..telemetry import TrainingStep
from .checkpoint import Dynamics
from .trajectories import fingerprint_episodes


def replay(brain: FullConnectomeBackend, episodes: list[list[TrainingStep]]) -> tuple[np.ndarray, np.ndarray]:
    activity, scores = [], []
    for episode in episodes:
        brain.reset()
        for step in episode:
            state = brain.step(make_stimulus(Observation.model_validate(step.observation)))
            activity.append(state.state_vector)
            scores.append([state.action_bias[tool] for tool in VALID_TOOLS])
    return np.asarray(activity), np.asarray(scores)


def imitation_metrics(scores: np.ndarray, labels: np.ndarray) -> dict:
    targets = np.zeros_like(scores)
    targets[np.arange(len(labels)), labels] = .8
    return {'mse': float(np.mean((scores - targets) ** 2)),
            'tool_accuracy': float(np.mean(np.argmax(scores, axis=1) == labels))}


def train_connectome(graph_path: Path, episodes: list[list[TrainingStep]], output: Path,
                     *, seed: int = 0, ridge: float = 1e-5) -> dict:
    if not episodes or any(not episode for episode in episodes):
        raise ValueError('Training requires nonempty episodes')
    if not np.isfinite(ridge) or ridge <= 0:
        raise ValueError('Ridge regularization must be finite and positive')
    labels = np.array([VALID_TOOLS.index(step.chosen_action['tool']) for episode in episodes for step in episode])
    brain = FullConnectomeBackend(seed=seed, connectome_path=graph_path)
    fingerprint = brain.graph.fingerprint
    _, initial_scores = replay(brain, episodes)
    frozen = imitation_metrics(initial_scores, labels)
    targets = np.zeros((len(labels), len(VALID_TOOLS)))
    targets[np.arange(len(labels)), labels] = np.arctanh(.8)
    best: tuple | None = None
    trials = []
    for gain in (.6, .9, 1.2):
        for leak in (.2, .3, .5):
            brain.dynamics = Dynamics(recurrent_gain=gain, leak=leak)
            features, _ = replay(brain, episodes)
            weights = np.linalg.solve(features.T @ features + ridge * np.eye(64), features.T @ targets).T
            metrics = imitation_metrics(np.tanh(features @ weights.T), labels)
            trials.append({'dynamics': brain.dynamics.model_dump(), **metrics})
            if best is None or metrics['mse'] < best[0]:
                best = (metrics['mse'], weights, brain.dynamics.model_copy())
    assert best is not None
    brain._readout[len(ALL_MODES):] = best[1]
    brain.dynamics = best[2]
    brain._learned_tools = True
    _, trained_scores = replay(brain, episodes)
    if brain.graph.fingerprint != fingerprint:
        raise RuntimeError('Training changed frozen graph')
    report = {'method': 'offline tool imitation; gain/leak grid and ridge readout',
              'seed': seed, 'ridge': ridge, 'episodes': len(episodes), 'steps': len(labels),
              'dataset_sha256': fingerprint_episodes(episodes),
              'graph_fingerprint': fingerprint, 'topology_hash': brain.graph.topology_hash,
              'frozen': frozen, 'trained': imitation_metrics(trained_scores, labels), 'trials': trials,
              'selected_dynamics': brain.dynamics.model_dump(),
              'task_success_improvement': None, 'evaluation': 'in-sample imitation only'}
    brain.save_checkpoint(output, metadata=report)
    (output / 'training.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    brain.close()
    return report
