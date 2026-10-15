"""Isolated multi-seed task/checkpoint comparisons (§62–66,80).

The current runner covers the two self-contained toy fixtures. It reports
compute differences; sharing a seed is not evidence of matched compute.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..agent import run_agent
from ..brain import BRAIN_REGISTRY, make_brain
from ..domain import TaskSpec
from ..environment import SoftwareEnvironment
from ..llm import HeuristicCandidateGenerator
from ..policy import ControlMode
from ..telemetry import TelemetryLogger
from .tournament import TournamentCortex


class ControllerSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(min_length=1)
    brain: str
    connectome_path: Path | None = None
    checkpoint: Path | None = None

    @model_validator(mode='after')
    def known_backend(self):
        if self.brain not in BRAIN_REGISTRY:
            raise ValueError('Unknown controller backend')
        if self.connectome_path is not None and self.brain not in {'full', 'reduced'}:
            raise ValueError('Only biological controllers accept a graph path')
        if self.checkpoint is not None and self.brain not in {'full', 'reduced', 'functional', 'functional_fly'}:
            raise ValueError('This controller does not support checkpoints')
        return self

    def create(self, seed: int):
        kwargs = {}
        if self.connectome_path is not None:
            kwargs['connectome_path'] = self.connectome_path.expanduser()
        if self.checkpoint is not None:
            kwargs['checkpoint_path'] = self.checkpoint.expanduser()
        return make_brain(self.brain, seed=seed, **kwargs)


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    controllers: list[ControllerSpec] = Field(default_factory=lambda: [
        ControllerSpec(id='functional', brain='functional'), ControllerSpec(id='fsm', brain='heuristic_fsm')], min_length=1)
    fixtures: list[Literal['toy_arithmetic_bug', 'toy_import_bug']] = Field(
        default=['toy_arithmetic_bug', 'toy_import_bug'], min_length=1)
    seeds: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1)
    controls: list[Literal['bias', 'hard']] = Field(default=['bias', 'hard'], min_length=1)
    cortex: Literal['heuristic', 'tournament'] = 'heuristic'
    max_steps: int = Field(default=25, ge=1, le=1000)
    max_tool_calls: int = Field(default=60, ge=1, le=1000)

    @model_validator(mode='after')
    def unique_cases(self):
        for values in ([item.id for item in self.controllers], self.fixtures, self.seeds, self.controls):
            if len(values) != len(set(values)):
                raise ValueError('Benchmark IDs, seeds, fixtures and controls must be unique')
        return self


def _checkpoint_hash(spec: ControllerSpec) -> str | None:
    if spec.checkpoint is None:
        return None
    path = spec.checkpoint.expanduser()
    filename = 'distillation.json' if spec.brain in {'functional', 'functional_fly'} else 'checkpoint.json'
    return hashlib.sha256((path / filename).read_bytes()).hexdigest()


async def run_benchmark(config: BenchmarkConfig, output: Path) -> dict:
    experiment_id = uuid4().hex
    directory = output / experiment_id
    directory.mkdir(parents=True)
    report: dict[str, Any] = {'experiment_id': experiment_id, 'config': config.model_dump(mode='json'),
              'comparison_scope': {'cortex': config.cortex, 'matched_compute': False,
                                   'note': 'Toy task evidence only; parameter, state and latency budgets are not matched.'},
              'cases': [], 'summary': {}}
    (directory / 'config.json').write_text(config.model_dump_json(indent=2))
    telemetry = TelemetryLogger(directory / 'trajectories')
    for spec in config.controllers:
        for seed in config.seeds:
            for fixture in config.fixtures:
                for control in config.controls:
                    run_id = uuid4().hex
                    repository = directory / 'repositories' / run_id
                    case: dict[str, Any] = {'controller_id': spec.id, 'seed': seed, 'fixture': fixture, 'control': control,
                            'repository': str(repository.resolve()), 'metrics': None, 'error': None}
                    brain = None
                    try:
                        brain = spec.create(seed)
                        brain.set_learning_mode(False)
                        cortex = TournamentCortex() if config.cortex == 'tournament' else HeuristicCandidateGenerator()
                        result = await run_agent(SoftwareEnvironment(repository, seed=seed), brain, cortex,
                            TaskSpec(id=run_id, task='Fix the bug and verify the result', fixture=fixture,
                                     seed=seed, max_steps=config.max_steps, max_tool_calls=config.max_tool_calls),
                            control_mode=ControlMode.HARD_CONTROL if control == 'hard' else ControlMode.BIAS_CONTROL,
                            telemetry=telemetry, run_id=run_id)
                        case.update(metrics=result.metrics.model_dump(mode='json'), controller=brain.describe(),
                                    checkpoint_sha256=_checkpoint_hash(spec),
                                    graph_fingerprint=brain.graph.fingerprint if hasattr(brain, 'graph') else None)
                    except (OSError, ValueError, RuntimeError) as exc:
                        case['error'] = f'{type(exc).__name__}: {exc}'
                    finally:
                        if brain is not None:
                            brain.close()
                    report['cases'].append(case)
                    (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    for spec in config.controllers:
        cases = [case for case in report['cases'] if case['controller_id'] == spec.id]
        metrics = [case['metrics'] for case in cases if case['metrics'] is not None]
        report['summary'][spec.id] = {
            'cases': len(cases), 'errors': sum(case['error'] is not None for case in cases),
            'success_rate': sum(m['verified_completion'] for m in metrics) / len(cases),
            'mean_steps': sum(m['steps'] for m in metrics) / len(metrics) if metrics else None,
            'mean_tool_calls': sum(m['tool_calls'] for m in metrics) / len(metrics) if metrics else None,
            'regressions': sum(m['regressions'] for m in metrics),
            'unsafe_proposals': sum(m['unsafe_proposals'] for m in metrics),
        }
    (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
