"""Recomputed, task-scoped admission from immutable experiment evidence (§60,86–88).

This checks measured compatibility, not malicious local users or general capability.
"""

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain import Observation
from ..telemetry import RunMetrics, TrainingStep
from .benchmark import BenchmarkConfig
from .cortex import CortexConfig
from .identity import file_sha256, implementation_fingerprint
from .shadow import analyze_shadow

AdmissionMode = Literal['bounded', 'autonomous']


class ValidationArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    format_version: Literal[1] = 1
    report_path: str
    report_sha256: str
    implementation_sha256: str
    controller_id: str
    baseline_id: str
    mode: AdmissionMode
    accepted: bool
    reasons: list[str]
    max_influence: float = Field(ge=0, le=1)
    scope: dict
    trajectories_sha256: dict[str, str]


def cortex_identity(config: CortexConfig) -> dict:
    if config.cortex != 'lmstudio':
        return {'cortex': config.cortex}
    return {'cortex': config.cortex, 'model': config.model, 'decision_model': config.decision_model,
            'base_url': (config.base_url or os.environ.get('LMSTUDIO_BASE_URL') or 'http://127.0.0.1:1234/v1').rstrip('/'),
            'request_timeout': config.request_timeout, 'max_model_calls': config.max_model_calls}


def _load_report(path: Path) -> dict:
    if path.stat().st_size > 32 * 1024 ** 2:
        raise ValueError('Experiment report exceeds size limit')
    report = json.loads(path.read_text())
    if not isinstance(report, dict):
        raise TypeError('Expected an experiment report object')
    return report


def _check_execution(case: dict, directory: Path, checksums: dict, config: BenchmarkConfig) -> RunMetrics:
    if case.get('error') or not isinstance(case.get('identity'), dict):
        raise ValueError('Missing execution or controller identity')
    run_id = case.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch('[a-f0-9]{32}', run_id):
        raise ValueError('Invalid execution ID')
    path = directory / (run_id + '.steps.jsonl')
    if path.is_symlink() or file_sha256(path) != checksums.get(path.name):
        raise ValueError('Trajectory checksum mismatch')
    steps = [TrainingStep.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
    metrics = RunMetrics.model_validate(case.get('metrics'))
    if (not metrics.verified_completion or metrics.task_id != run_id or metrics.regressions
            or metrics.unsafe_proposals or metrics.model_errors or metrics.online_updates or metrics.online_rollbacks
            or metrics.tool_calls != len(steps) or metrics.steps != len(steps) or not steps
            or metrics.steps > config.max_steps or metrics.tool_calls > config.max_tool_calls
            or sum(metrics.model_calls.values()) > config.max_model_calls
            or any(not math.isfinite(v) or v < 0 for v in metrics.model_dump().values()
                   if isinstance(v, (int, float)))
            or steps[-1].chosen_action.get('tool') != 'stop' or not steps[-1].outcome.get('success')
            or not Observation.model_validate(steps[-1].observation).is_healthy):
        raise ValueError('Execution lacks clean verified completion within its budgets')
    return metrics


def _assess(path: Path, controller_id: str, baseline_id: str, mode: AdmissionMode) -> dict:
    report = _load_report(path)
    reasons: list[str] = []
    result: dict[str, Any] = {'accepted': False, 'reasons': reasons, 'max_influence': 0., 'scope': {},
                             'implementation_sha256': implementation_fingerprint(), 'trajectories_sha256': {}}
    try:
        if (mode not in {'bounded', 'autonomous'} or report.get('format_version') != 1
                or report.get('completed') is not True or report.get('source_unchanged') is not True
                or report.get('implementation_sha256') != result['implementation_sha256']):
            raise ValueError('Incomplete or stale implementation evidence')
        config = BenchmarkConfig.model_validate(report['config'])
        specs = {spec.id: spec for spec in config.controllers}
        target = specs[controller_id]
        if controller_id == baseline_id or baseline_id not in specs:
            raise ValueError('A distinct paired baseline is required')
        if len(config.seeds) < 3 or len(config.fixtures) < 2:
            raise ValueError('Admission needs at least three seeds and two fixture families')
        control = 'bias' if mode == 'bounded' else 'hard'
        if control not in config.controls or 'bias' not in config.controls:
            raise ValueError('Missing target or baseline control mode')
        if mode == 'bounded' and target.observer is None:
            raise ValueError('Bounded admission requires an observed controller')
        if mode == 'autonomous' and target.brain not in {'functional', 'functional_fly', 'full', 'reduced'}:
            raise ValueError('Autonomous admission requires a fly controller')
        groups = [[case for case in report['cases'] if case['controller_id'] == name and case['control'] == selected]
                  for name, selected in ((controller_id, control), (baseline_id, 'bias'))]
        expected = {(seed, fixture) for seed in config.seeds for fixture in config.fixtures}
        checksums = report.get('trajectories_sha256', {})
        directory = path.parent / 'trajectories'
        if file_sha256(directory / 'runs.jsonl') != checksums.get('runs.jsonl'):
            raise ValueError('Metrics checksum mismatch')
        recorded = {item['task_id']: item for item in map(json.loads, (directory / 'runs.jsonl').read_text().splitlines())}
        for cases in groups:
            if len(cases) != len(expected) or {(case['seed'], case['fixture']) for case in cases} != expected:
                raise ValueError('Incomplete or duplicate paired task coverage')
            for case in cases:
                metrics = _check_execution(case, directory, checksums, config)
                if metrics.model_dump(mode='json') != recorded.get(case['run_id']):
                    raise ValueError('Metrics do not match the durable execution record')
        baselines = {(case['seed'], case['fixture']): case for case in groups[1]}
        for case in groups[0]:
            baseline = baselines[case['seed'], case['fixture']]
            if case['metrics']['tool_calls'] > baseline['metrics']['tool_calls']:
                raise ValueError('Target uses more tool calls than its paired baseline')
            if mode == 'bounded' and case['identity']['active'] != baseline['identity']['active']:
                raise ValueError('Shadow and baseline active controllers differ')
        if mode == 'bounded':
            shadow = analyze_shadow(directory, {case['run_id'] for case in groups[0]})
            if (shadow['attempted_decisions'] < 30 or shadow['coverage'] != 1. or shadow['invalid_records']
                    or shadow['legacy_steps'] or shadow['observer_errors']
                    or shadow['attempted_decisions'] != sum(case['metrics']['tool_calls'] for case in groups[0])):
                raise ValueError('Insufficient or incomplete observer telemetry')
            previous = getattr(target, 'influence', 0.)
            result['max_influence'] = {0.: .1, .1: .25, .25: .5, .5: 1., 1.: 1.}[previous]
        else:
            result['max_influence'] = 1.
        result['scope'] = {'fixtures': config.fixtures, 'seeds': config.seeds, 'control': control,
            'cortex': cortex_identity(config), 'max_steps': config.max_steps, 'max_tool_calls': config.max_tool_calls,
            'identities': [{'seed': case['seed'], 'identity': case['identity']} for case in groups[0]],
            'limitation': 'Synthetic fixture scope only; no admission for arbitrary repositories or general superiority.'}
        result['trajectories_sha256'] = checksums
        result['accepted'] = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reasons.append(str(exc))
    return result


def create_validation(report_path: Path, controller_id: str, baseline_id: str, output: Path,
                      *, mode: AdmissionMode = 'bounded') -> dict:
    verdict = _assess(report_path, controller_id, baseline_id, mode)
    artifact = ValidationArtifact(report_path=os.path.relpath(report_path.resolve(), output.parent.resolve()),
        report_sha256=file_sha256(report_path), controller_id=controller_id, baseline_id=baseline_id,
        mode=mode, **verdict)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2))
    return artifact.model_dump(mode='json')


def authorize(path: Path, *, identity: dict, config: CortexConfig, fixture: str | None, seed: int,
              control: str, max_steps: int, max_tool_calls: int, mode: AdmissionMode, influence: float = 0.) -> dict:
    artifact = ValidationArtifact.model_validate_json(path.read_text())
    report_path = path.parent / artifact.report_path
    if file_sha256(report_path) != artifact.report_sha256:
        raise ValueError('Source report checksum mismatch')
    verdict = _assess(report_path, artifact.controller_id, artifact.baseline_id, mode)
    if (not artifact.accepted or artifact.mode != mode or not verdict['accepted']
            or artifact.implementation_sha256 != verdict['implementation_sha256']
            or artifact.trajectories_sha256 != verdict['trajectories_sha256']):
        raise ValueError('Admission rejected: ' + '; '.join(verdict['reasons']))
    scope = verdict['scope']
    if (fixture not in scope['fixtures'] or seed not in scope['seeds'] or control != scope['control']
            or cortex_identity(config) != scope['cortex'] or max_steps != scope['max_steps']
            or max_tool_calls != scope['max_tool_calls']
            or not any(item['seed'] == seed and item['identity'] == identity for item in scope['identities'])
            or not math.isfinite(influence) or influence not in {0., .1, .25, .5, 1.}
            or influence > verdict['max_influence']):
        raise ValueError('Request does not match the validated controller/task/model/budget scope')
    return verdict
