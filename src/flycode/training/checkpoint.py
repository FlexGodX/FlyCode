"""Validated numeric parameters bound to one immutable connectome artifact."""

import hashlib
import json
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..brain.functional_fly import ALL_MODES
from ..domain import VALID_TOOLS


class Dynamics(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    recurrent_gain: float = Field(default=.9, ge=0, le=1.5)
    leak: float = Field(default=.3, gt=0, le=1)
    input_gain: float = Field(default=1., gt=0, le=2)


def save_parameters(directory: Path, graph_fingerprint: str, input_mapping: np.ndarray,
                    readout: np.ndarray, dynamics: Dynamics, *, learned_tools: bool,
                    metadata: dict | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'parameters.npz'
    np.savez_compressed(path, input=input_mapping, readout=readout)
    manifest = {'format_version': 1, 'graph_fingerprint': graph_fingerprint,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'dynamics': dynamics.model_dump(), 'learned_tools': learned_tools,
                'modes': list(ALL_MODES), 'tools': list(VALID_TOOLS), 'metadata': metadata or {}}
    (directory / 'checkpoint.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def load_parameters(directory: Path, graph_fingerprint: str, neurons: int) -> tuple:
    try:
        manifest = json.loads((directory / 'checkpoint.json').read_text())
        if manifest.get('format_version') != 1 or manifest.get('graph_fingerprint') != graph_fingerprint:
            raise ValueError('Checkpoint does not match this graph')
        path = directory / 'parameters.npz'
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get('sha256'):
            raise ValueError('Checkpoint parameters checksum mismatch')
        with np.load(path, allow_pickle=False) as archive:
            input_mapping, readout = archive['input'].copy(), archive['readout'].copy()
        if (input_mapping.shape != (neurons, 13)
                or readout.shape != (len(ALL_MODES) + len(VALID_TOOLS), 64)
                or any(array.dtype.kind not in 'fiu' or not np.all(np.isfinite(array))
                       or np.any(np.abs(array) > np.finfo(np.float32).max)
                       for array in (input_mapping, readout))):
            raise ValueError('Invalid checkpoint parameters')
        if (manifest.get('tools') != list(VALID_TOOLS) or manifest.get('modes') != list(ALL_MODES)
                or type(manifest.get('learned_tools')) is not bool):
            raise ValueError('Checkpoint output vocabulary mismatch')
        return (input_mapping.astype(np.float32), readout.astype(np.float32),
                Dynamics.model_validate(manifest['dynamics']), manifest['learned_tools'])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Cannot load controller checkpoint: {exc}') from exc
