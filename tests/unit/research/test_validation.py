"""Scoped admission is derived from actual paired runs, never a trusted boolean."""

import asyncio
import json

import numpy as np
import pytest


async def test_validation_binds_real_shadow_data_to_controller_task_and_model_scope(tmp_path):
    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.research.benchmark import (
        BenchmarkConfig,
        ControllerSpec,
        run_benchmark,
    )
    from flycode.research.cortex import CortexConfig
    from flycode.research.validation import authorize, create_validation

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    config = BenchmarkConfig.model_validate({'controllers': [
        {'id': 'baseline', 'brain': 'functional'},
        {'id': 'observed', 'brain': 'functional', 'observer': {'brain': 'full', 'connectome_path': str(graph)}}],
        'fixtures': ['toy_arithmetic_bug', 'toy_import_bug'], 'seeds': [0, 1, 2], 'controls': ['bias']})
    report = await run_benchmark(config, tmp_path / 'runs')
    report_path = tmp_path / 'runs' / report['experiment_id'] / 'report.json'
    artifact = tmp_path / 'validation.json'
    verdict = create_validation(report_path, 'observed', 'baseline', artifact, mode='bounded')
    assert verdict['accepted'], verdict
    assert verdict['max_influence'] == .1  # Clean shadow evidence admits the first step only.
    spec = ControllerSpec.model_validate(config.controllers[1].model_dump())
    brain = spec.create(0)
    identity = spec.identity(brain)
    request = {'identity': identity, 'config': CortexConfig(), 'fixture': 'toy_arithmetic_bug', 'seed': 0,
               'control': 'bias', 'max_steps': 25, 'max_tool_calls': 60, 'mode': 'bounded', 'influence': .1}
    assert authorize(artifact, **request)['accepted']
    from flycode.cli import main

    cli_args = ['run', '--task', 'fix', '--shadow-brain', 'full', '--shadow-connectome-path', str(graph),
                '--validation-report', str(artifact), '--influence', '.1']
    telemetry = tmp_path / 'cli-telemetry'
    assert await asyncio.to_thread(main, [*cli_args, '--repo', str(tmp_path / 'cli-repo'),
        '--fixture', 'toy_arithmetic_bug', '--telemetry-dir', str(telemetry)]) == 0
    first = json.loads((telemetry / 'run.steps.jsonl').read_text().splitlines()[0])
    assert json.loads((telemetry / 'run.admission.json').read_text())['accepted']
    assert first['rollout_decision']['requested_influence'] == .1
    assert first['rollout_decision']['applied_influence'] == 0.
    assert await asyncio.to_thread(main, [*cli_args, '--repo', str(tmp_path / 'unvalidated')]) == 2
    assert not (tmp_path / 'unvalidated').exists()

    for change in [{'fixture': None}, {'fixture': 'search_helper_bug'}, {'seed': 9}, {'influence': .25},
                   {'control': 'hard'}, {'max_steps': 26}, {'config': CortexConfig(cortex='tournament')}]:
        with pytest.raises(ValueError):
            authorize(artifact, **{**request, **change})
    changed = json.loads(json.dumps(identity))
    changed['observer']['graph_fingerprint'] = 'another-graph'
    with pytest.raises(ValueError):
        authorize(artifact, **{**request, 'identity': changed})
    # Changing the source report after creating admission invalidates its checksum.
    report['summary']['observed']['success_rate'] = 0.
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='checksum'):
        authorize(artifact, **request)
    brain.close()


async def test_insufficient_report_cannot_unlock_influence_by_editing_accepted_flag(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.research.cortex import CortexConfig
    from flycode.research.validation import authorize, create_validation

    config = BenchmarkConfig.model_validate({'controllers': [{'id': 'baseline', 'brain': 'functional'},
        {'id': 'observed', 'brain': 'functional', 'observer': {'brain': 'mock'}}],
        'fixtures': ['toy_arithmetic_bug'], 'seeds': [0], 'controls': ['bias'], 'max_steps': 1})
    report = await run_benchmark(config, tmp_path / 'runs')
    artifact = tmp_path / 'validation.json'
    verdict = create_validation(tmp_path / 'runs' / report['experiment_id'] / 'report.json',
                                'observed', 'baseline', artifact, mode='bounded')
    assert not verdict['accepted']
    verdict['accepted'] = True
    verdict['reasons'] = []
    artifact.write_text(json.dumps(verdict))
    with pytest.raises(ValueError):
        authorize(artifact, identity=report['cases'][1]['identity'], config=CortexConfig(),
                  fixture='toy_arithmetic_bug', seed=0, control='bias', max_steps=1, max_tool_calls=60,
                  mode='bounded', influence=.1)


async def test_clean_runs_without_actual_interventions_cannot_promote_the_next_level(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.research.validation import create_validation

    config = BenchmarkConfig.model_validate({'controllers': [{'id': 'baseline', 'brain': 'functional'},
        {'id': 'bounded', 'brain': 'functional', 'observer': {'brain': 'mock'}, 'influence': .1}],
        'fixtures': ['toy_arithmetic_bug', 'toy_import_bug'], 'seeds': [0, 1, 2], 'controls': ['bias']})
    report = await run_benchmark(config, tmp_path / 'runs')
    assert report['summary']['bounded']['success_rate'] == 1.
    assert sum(case['metrics']['rollout_interventions'] for case in report['cases']) == 0
    verdict = create_validation(tmp_path / 'runs' / report['experiment_id'] / 'report.json',
                                'bounded', 'baseline', tmp_path / 'admission.json', mode='bounded')
    assert not verdict['accepted']


async def test_guarded_autonomy_admission_opens_only_matching_fixture_execution(tmp_path):
    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.cli import main
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.research.validation import create_validation

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    config = BenchmarkConfig.model_validate({'controllers': [{'id': 'baseline', 'brain': 'functional'},
        {'id': 'deployed', 'brain': 'full', 'connectome_path': str(graph), 'autonomous': True},
        {'id': 'research-only', 'brain': 'full', 'connectome_path': str(graph)}],
        'fixtures': ['toy_arithmetic_bug', 'toy_import_bug'], 'seeds': [0, 1, 2], 'controls': ['bias', 'hard']})
    report = await run_benchmark(config, tmp_path / 'runs')
    guarded = [case for case in report['cases'] if case['controller_id'] == 'deployed']
    assert len(guarded) == 6
    assert all(case['control'] == 'hard' and case['metrics']['autonomy_rollbacks'] == 0 for case in guarded)
    report_path = tmp_path / 'runs' / report['experiment_id'] / 'report.json'
    artifact = tmp_path / 'autonomy.json'
    assert create_validation(report_path, 'deployed', 'baseline', artifact, mode='autonomous')['accepted']
    assert not create_validation(report_path, 'research-only', 'baseline', tmp_path / 'research.json',
                                 mode='autonomous')['accepted']
    args = ['run', '--task', 'fix', '--brain', 'full', '--connectome-path', str(graph),
            '--autonomous', '--validation-report', str(artifact)]
    telemetry = tmp_path / 'live-telemetry'
    assert await asyncio.to_thread(main, [*args, '--repo', str(tmp_path / 'live'),
        '--fixture', 'toy_arithmetic_bug', '--telemetry-dir', str(telemetry)]) == 0
    first = json.loads((telemetry / 'run.steps.jsonl').read_text().splitlines()[0])
    assert json.loads((telemetry / 'run.admission.json').read_text())['accepted']
    assert first['autonomy_decision']['applied_control'] == 'hard_control'
    assert await asyncio.to_thread(main, [*args, '--repo', str(tmp_path / 'unvalidated')]) == 2
    assert not (tmp_path / 'unvalidated').exists()

    # Relabeling legacy hard runs cannot manufacture guard telemetry.
    report['config']['controllers'][2]['autonomous'] = True
    report_path.write_text(json.dumps(report))
    assert not create_validation(report_path, 'research-only', 'baseline', tmp_path / 'relabelled.json',
                                 mode='autonomous')['accepted']


def test_local_model_admission_binds_the_exact_instruction():
    from flycode.research.validation import task_matches

    assert task_matches('lmstudio', 'Fix arithmetic', 'Fix arithmetic')
    assert not task_matches('lmstudio', 'Fix arithmetic', 'Delete tests')
    assert not task_matches('lmstudio', 'Fix arithmetic', None)
    # The deterministic fixture cortex never consumes the task instruction.
    assert task_matches('heuristic', 'Fix arithmetic', 'fix')
