"""Isolated multi-seed task/checkpoint comparisons (§62–66,80).

The runner covers self-contained teacher and held-out synthetic fixtures. It reports
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
from ..policy import ControlMode
from ..policy.rollout import RolloutConfig
from ..telemetry import TelemetryLogger
from .cortex import CortexConfig, FixtureName, fixture_split
from .identity import file_sha256, implementation_fingerprint


class BrainSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    brain: str
    connectome_path: Path | None = None
    checkpoint: Path | None = None
    lesion_fraction: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def known_backend(self):
        if self.brain not in BRAIN_REGISTRY:
            raise ValueError('Unknown controller backend')
        if self.connectome_path is not None and self.brain not in {'full', 'reduced'}:
            raise ValueError('Only biological controllers accept a graph path')
        if self.checkpoint is not None and self.brain not in {'full', 'reduced', 'functional', 'functional_fly'}:
            raise ValueError('This controller does not support checkpoints')
        if self.lesion_fraction is not None and self.brain not in {'full', 'reduced'}:
            raise ValueError('Lesion requires a biological controller')
        return self

    def create(self, seed: int):
        kwargs = {}
        if self.connectome_path is not None:
            kwargs['connectome_path'] = self.connectome_path.expanduser()
        if self.checkpoint is not None:
            kwargs['checkpoint_path'] = self.checkpoint.expanduser()
        brain = make_brain(self.brain, seed=seed, **kwargs)
        if self.lesion_fraction is not None:
            from ..brain.full_connectome.sparse_backend import FullConnectomeBackend
            from .perturbation import apply_lesion

            if not isinstance(brain, FullConnectomeBackend):
                raise ValueError('Lesion requires a sparse biological controller')
            brain.graph.metadata['perturbation'] = apply_lesion(brain, self.lesion_fraction, seed)
        return brain

    def identity(self, brain) -> dict:
        return {'brain': self.brain, 'seed': brain.seed,
                'graph_fingerprint': brain.graph.fingerprint if hasattr(brain, 'graph') else None,
                'checkpoint_sha256': _checkpoint_hash(self), 'lesion_fraction': self.lesion_fraction,
                'policy': brain.snapshot_policy().model_dump() if hasattr(brain, 'snapshot_policy') else None}


class ControllerSpec(BrainSpec):
    id: str = Field(min_length=1)
    observer: BrainSpec | None = None
    influence: float = Field(default=0., ge=0, le=1, allow_inf_nan=False)
    autonomous: bool = False

    @model_validator(mode='after')
    def observed_influence(self):
        RolloutConfig(influence=self.influence)
        if self.influence and self.observer is None:
            raise ValueError('Nonzero influence requires an observer')
        if self.autonomous and self.influence:
            raise ValueError('Autonomy cannot mix bounded observer influence')
        return self

    def create(self, seed: int):
        active = super().create(seed)
        if self.observer is None:
            return active
        from ..brain.shadow import ShadowBackend

        try:
            return ShadowBackend(active, self.observer.create(seed), influence=self.influence)
        except (OSError, ValueError, RuntimeError):
            active.close()
            raise

    def identity(self, brain) -> dict:
        if self.observer is None:
            return {'active': super().identity(brain), 'observer': None}
        return {'active': super().identity(brain.active), 'observer': self.observer.identity(brain.observer)}


class BenchmarkConfig(CortexConfig):
    model_config = ConfigDict(extra='forbid')

    controllers: list[ControllerSpec] = Field(default_factory=lambda: [
        ControllerSpec(id='functional', brain='functional'), ControllerSpec(id='fsm', brain='heuristic_fsm')], min_length=1)
    fixtures: list[FixtureName] = Field(
        default=['toy_arithmetic_bug', 'toy_import_bug'], min_length=1)
    seeds: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1)
    controls: list[Literal['bias', 'hard']] = Field(default=['bias', 'hard'], min_length=1)
    max_steps: int = Field(default=25, ge=1, le=1000)
    max_tool_calls: int = Field(default=60, ge=1, le=1000)
    task: str = Field(default='Fix the bug and verify the result', min_length=1, max_length=10000)

    @model_validator(mode='after')
    def unique_cases(self):
        for values in ([item.id for item in self.controllers], self.fixtures, self.seeds, self.controls):
            if len(values) != len(set(values)):
                raise ValueError('Benchmark IDs, seeds, fixtures and controls must be unique')
        if any(spec.autonomous for spec in self.controllers) and 'hard' not in self.controls:
            raise ValueError('Autonomous profiles require hard control in the experiment')
        return self


def _checkpoint_hash(spec: BrainSpec) -> str | None:
    if spec.checkpoint is None:
        return None
    path = spec.checkpoint.expanduser()
    filename = 'distillation.json' if spec.brain in {'functional', 'functional_fly'} else 'checkpoint.json'
    return hashlib.sha256((path / filename).read_bytes()).hexdigest()


async def run_benchmark(config: BenchmarkConfig, output: Path) -> dict:
    experiment_id = uuid4().hex
    directory = output / experiment_id
    directory.mkdir(parents=True)
    report: dict[str, Any] = {'format_version': 1, 'experiment_id': experiment_id, 'completed': False,
              'implementation_sha256': implementation_fingerprint(), 'source_unchanged': None,
              'config': config.model_dump(mode='json'),
              'comparison_scope': {'cortex': config.cortex, 'matched_compute': False,
                                   'training_budget_matched': False,
                                   'note': 'Synthetic task evidence only; parameter, state, latency and training budgets are not matched.'},
              'cases': [], 'summary': {}}
    (directory / 'config.json').write_text(config.model_dump_json(indent=2))
    telemetry = TelemetryLogger(directory / 'trajectories')
    for spec in config.controllers:
        for seed in config.seeds:
            for fixture in config.fixtures:
                for control in config.controls:
                    if spec.autonomous and control != 'hard':
                        continue
                    run_id = uuid4().hex
                    repository = directory / 'repositories' / run_id
                    case: dict[str, Any] = {'controller_id': spec.id, 'seed': seed, 'fixture': fixture, 'control': control,
                            'run_id': run_id, 'identity': None,
                            'fixture_split': fixture_split(fixture),
                            'repository': str(repository.resolve()), 'metrics': None, 'error': None}
                    brain = None
                    try:
                        brain = spec.create(seed)
                        brain.set_learning_mode(False)
                        case['identity'] = spec.identity(brain)
                        cortex = config.create_cortex()
                        result = await run_agent(SoftwareEnvironment(repository, seed=seed), brain, cortex,
                            TaskSpec(id=run_id, task=config.task, fixture=fixture,
                                     seed=seed, max_steps=config.max_steps, max_tool_calls=config.max_tool_calls),
                            control_mode=ControlMode.HARD_CONTROL if control == 'hard' else ControlMode.BIAS_CONTROL,
                            telemetry=telemetry, run_id=run_id, autonomous_guard=spec.autonomous)
                        case.update(metrics=result.metrics.model_dump(mode='json'), controller=brain.describe(),
                                    perturbation=brain.graph.metadata.get('perturbation') if hasattr(brain, 'graph') else None,
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
            'rollout_interventions': sum(m['rollout_interventions'] for m in metrics),
            'rollout_rollbacks': sum(m['rollout_rollbacks'] for m in metrics),
            'shadow_decisions': sum(m['shadow_decisions'] for m in metrics),
            'autonomy_rollbacks': sum(m['autonomy_rollbacks'] for m in metrics),
        }
    report['completed'] = True
    report['source_unchanged'] = implementation_fingerprint() == report['implementation_sha256']
    report['trajectories_sha256'] = {path.name: file_sha256(path) for path in (directory / 'trajectories').glob('*.jsonl')}
    (directory / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
