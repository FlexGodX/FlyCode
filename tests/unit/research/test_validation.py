"""Scoped admission is derived from actual paired runs, never a trusted boolean."""

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
