"""Imitate selected teacher tool biases, with an episode-level holdout (§82)."""

import hashlib
from pathlib import Path

import numpy as np

from ..brain.full_connectome.sparse_backend import FullConnectomeBackend
from ..domain import Observation
from ..telemetry import TrainingStep
from .constrained import replay
from .student import save_student, student_features
from .trajectories import fingerprint_episodes


def _features(episodes: list[list[TrainingStep]]) -> np.ndarray:
    return np.array([student_features(Observation.model_validate(step.observation))
                     for episode in episodes for step in episode])


def _metrics(predicted: np.ndarray, teacher: np.ndarray) -> dict:
    return {'rmse': float(np.sqrt(np.mean((predicted - teacher) ** 2))),
            'tool_agreement': float(np.mean(np.argmax(predicted, axis=1) == np.argmax(teacher, axis=1)))}


def distill_controller(graph_path: Path, teacher_checkpoint: Path, episodes: list[list[TrainingStep]],
                       output: Path, *, max_rmse: float = .1, min_agreement: float = .9,
                       ridge: float = 1e-5, seed: int = 0) -> dict:
    if len(episodes) < 2 or any(not episode for episode in episodes):
        raise ValueError('Distillation requires at least two nonempty independent episodes')
    if (not np.isfinite([max_rmse, min_agreement, ridge]).all()
            or max_rmse < 0 or not 0 <= min_agreement <= 1 or ridge <= 0):
        raise ValueError('Invalid distillation tolerance or regularization')
    teacher = FullConnectomeBackend(connectome_path=graph_path, checkpoint_path=teacher_checkpoint)
    if not teacher._learned_tools:
        raise ValueError('Distillation requires an offline-trained teacher checkpoint')
    order = np.random.default_rng(seed).permutation(len(episodes))
    n_validation = max(1, len(episodes) // 4)
    train = [episodes[i] for i in order[n_validation:]]
    validation = [episodes[i] for i in order[:n_validation]]
    _, targets = replay(teacher, train)
    x = _features(train)
    weights = np.linalg.solve(x.T @ x + ridge * np.eye(x.shape[1]),
                              x.T @ np.arctanh(np.clip(targets, -.999, .999))).T
    _, heldout_targets = replay(teacher, validation)
    training = _metrics(np.tanh(x @ weights.T), targets)
    heldout = _metrics(np.tanh(_features(validation) @ weights.T), heldout_targets)
    report = {'behavior': 'tool bias only; original Functional Fly mode arbitration is retained',
              'teacher_graph': teacher.graph.fingerprint, 'train_episodes': len(train),
              'teacher_checkpoint_sha256': hashlib.sha256((teacher_checkpoint / 'checkpoint.json').read_bytes()).hexdigest(),
              'dataset_sha256': fingerprint_episodes(episodes),
              'validation_episodes': len(validation), 'train_indices': order[n_validation:].tolist(),
              'validation_indices': order[:n_validation].tolist(), 'seed': seed, 'ridge': ridge,
              'max_rmse': max_rmse, 'min_agreement': min_agreement,
              'training': training, 'validation': heldout,
              'accepted': heldout['rmse'] <= max_rmse and heldout['tool_agreement'] >= min_agreement}
    save_student(output, weights, report)
    teacher.close()
    return report
