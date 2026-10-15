"""Load executed decisions, retaining resets as independent episodes (§53)."""

import hashlib
import json
from pathlib import Path

from ..domain import VALID_TOOLS, Observation
from ..telemetry import TrainingStep


def fingerprint_episodes(episodes: list[list[TrainingStep]]) -> str:
    records = [[step.model_dump(mode='json') for step in episode] for episode in episodes]
    return hashlib.sha256(json.dumps(records, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load_episodes(directory: Path) -> list[list[TrainingStep]]:
    episodes: list[list[TrainingStep]] = []
    for path in sorted(directory.rglob('*.steps.jsonl')):
        episode: list[TrainingStep] = []
        previous = -1
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            step = TrainingStep.model_validate_json(line)
            obs = Observation.model_validate(step.observation)
            if step.chosen_action.get('tool') not in VALID_TOOLS:
                raise ValueError(f'Unknown training tool in {path}')
            if obs.step_number <= previous and episode:
                episodes.append(episode)
                episode = []
            episode.append(step)
            previous = obs.step_number
        if episode:
            episodes.append(episode)
    if not episodes:
        raise ValueError(f'No executed trajectories in {directory}')
    return episodes
