"""Small, validated Functional Fly readout for selected teacher tool biases."""

import hashlib
import json
from pathlib import Path

import numpy as np

from ..cognition.mushroom_body import MushroomBody
from ..domain import VALID_TOOLS, Observation


def student_features(observation: Observation) -> np.ndarray:
    return np.append(MushroomBody.features(observation), 1.)


def save_student(directory: Path, weights: np.ndarray, report: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'student.npy'
    np.save(path, weights, allow_pickle=False)
    (directory / 'distillation.json').write_text(json.dumps({**report, 'format_version': 1,
        'tools': list(VALID_TOOLS), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, indent=2))


def load_student(directory: Path) -> np.ndarray:
    try:
        report = json.loads((directory / 'distillation.json').read_text())
        if report.get('format_version') != 1 or report.get('accepted') is not True:
            raise ValueError('Student has not passed held-out validation')
        path = directory / 'student.npy'
        if report.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Student parameters checksum mismatch')
        weights = np.load(path, allow_pickle=False)
        if (weights.shape != (len(VALID_TOOLS), 25) or not np.all(np.isfinite(weights))
                or report.get('tools') != list(VALID_TOOLS)):
            raise ValueError('Invalid student parameters')
        return weights
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f'Cannot load distilled controller: {exc}') from exc
