"""Actual repeated-family repairs with retained MB/RPE and frozen controls (§84)."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from ..agent import run_agent
from ..brain.functional_fly import FunctionalFlyBackend
from ..domain import TaskSpec
from ..environment import SoftwareEnvironment
from ..memory import EpisodicMemory
from ..policy import ControlMode
from ..telemetry import TelemetryLogger
from .cortex import CortexConfig, FixtureName, fixture_split


class AdaptationConfig(CortexConfig):
    seeds: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1)
    fixtures: list[FixtureName] = Field(default=['toy_arithmetic_bug', 'toy_import_bug'], min_length=1)
    repeats: int = Field(default=3, ge=2, le=20)
    control: Literal['bias', 'hard'] = 'bias'
    checkpoint: Path | None = None
    max_steps: int = Field(default=25, ge=1, le=1000)
    max_tool_calls: int = Field(default=60, ge=1, le=1000)

    @model_validator(mode='after')
    def unique_sequence(self):
        if len(set(self.seeds)) != len(self.seeds) or len(set(self.fixtures)) != len(self.fixtures):
            raise ValueError('Seeds and fixture names must be unique; use repeats for repetition')
        return self


def _policy_hash(brain: FunctionalFlyBackend) -> str:
    data = json.dumps(brain.snapshot_policy().model_dump(), sort_keys=True, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def _aggregate(cases: list[dict]) -> dict:
    metrics = [case['metrics'] for case in cases if case['metrics'] is not None]
    return {'cases': len(cases), 'errors': sum(case['error'] is not None for case in cases),
            'success_rate': sum(metric['verified_completion'] for metric in metrics) / len(cases),
            'mean_tool_calls': sum(metric['tool_calls'] for metric in metrics) / len(metrics) if metrics else None,
            'mean_steps': sum(metric['steps'] for metric in metrics) / len(metrics) if metrics else None,
            'updates': sum(metric['online_updates'] for metric in metrics),
            'rollbacks': sum(metric['online_rollbacks'] for metric in metrics)}


async def run_adaptation(config: AdaptationConfig, output: Path) -> dict:
    directory = output / uuid4().hex
    directory.mkdir(parents=True)
    (directory / 'config.json').write_text(config.model_dump_json(indent=2))
    telemetry = TelemetryLogger(directory / 'trajectories')
    report: dict[str, Any] = {'experiment_id': directory.name, 'config': config.model_dump(mode='json'),
        'scope': 'Synthetic repeated families; cortex and episodic memory are fresh each episode.',
        'cases': [], 'summary': {}, 'faster_adaptation_demonstrated': False}
    for seed in config.seeds:
        for learning in (False, True):
            brain = None
            setup_error = None
            try:
                brain = FunctionalFlyBackend(seed=seed,
                    checkpoint_path=config.checkpoint.expanduser() if config.checkpoint else None)
                brain.set_learning_mode(learning)
            except (OSError, ValueError, RuntimeError) as exc:
                setup_error = f'{type(exc).__name__}: {exc}'
            try:
                for repeat in range(config.repeats):
                    for fixture in config.fixtures:
                        run_id = uuid4().hex
                        repo = directory / 'repositories' / run_id
                        case: dict[str, Any] = {'seed': seed, 'learning': learning, 'repeat': repeat,
                            'fixture': fixture, 'fixture_split': fixture_split(fixture),
                            'repository': str(repo.resolve()), 'metrics': None, 'error': setup_error,
                            'policy_before': None, 'policy_after': None, 'guard': None}
                        if brain is not None:
                            case['policy_before'] = _policy_hash(brain)
                            try:
                                result = await run_agent(SoftwareEnvironment(repo, seed=seed), brain,
                                    config.create_cortex(), TaskSpec(id=run_id,
                                        task='Fix the bug and verify the result', fixture=fixture, seed=seed,
                                        max_steps=config.max_steps, max_tool_calls=config.max_tool_calls),
                                    control_mode=ControlMode.HARD_CONTROL if config.control == 'hard' else ControlMode.BIAS_CONTROL,
                                    episodic_memory=EpisodicMemory(), telemetry=telemetry, run_id=run_id)
                                case['metrics'] = result.metrics.model_dump(mode='json')
                            except (OSError, ValueError, RuntimeError) as exc:
                                case['error'] = f'{type(exc).__name__}: {exc}'
                            case['policy_after'] = _policy_hash(brain)
                            case['guard'] = brain.adaptation.state.model_dump()
                        report['cases'].append(case)
                        (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
                if brain is not None and learning:
                    brain.save_learning(directory / 'memory' / str(seed))
            finally:
                if brain is not None:
                    brain.close()
    for learning, name in ((False, 'frozen'), (True, 'learning')):
        cases = [case for case in report['cases'] if case['learning'] == learning]
        report['summary'][name] = {**_aggregate(cases),
            'first': _aggregate([case for case in cases if case['repeat'] == 0]),
            'last': _aggregate([case for case in cases if case['repeat'] == config.repeats - 1])}
    if len(config.seeds) >= 3 and all(report['summary'][name]['success_rate'] == 1. for name in ('frozen', 'learning')):
        improvements = []
        for seed in config.seeds:
            final = [case for case in report['cases'] if case['seed'] == seed and case['repeat'] == config.repeats - 1]
            frozen = _aggregate([case for case in final if not case['learning']])
            learned = _aggregate([case for case in final if case['learning']])
            improvements.append(learned['mean_tool_calls'] < frozen['mean_tool_calls'])
        report['faster_adaptation_demonstrated'] = all(improvements)
    (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
