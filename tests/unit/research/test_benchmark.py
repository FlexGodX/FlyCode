"""Experiments retain actual outcomes and compare identical explicit conditions."""

import json

import pytest


def test_benchmark_config_rejects_ambiguous_or_invalid_cases():
    from flycode.research.benchmark import BenchmarkConfig

    for change in [{'seeds': []}, {'seeds': [0, 0]}, {'fixtures': ['not-a-fixture']},
                   {'controllers': [{'id': 'same', 'brain': 'mock'}, {'id': 'same', 'brain': 'functional'}]}]:
        with pytest.raises(ValueError):
            BenchmarkConfig.model_validate(change)


async def test_benchmark_records_success_and_failure_in_isolated_repositories(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark

    config = BenchmarkConfig.model_validate({
        'controllers': [{'id': 'fsm', 'brain': 'heuristic_fsm'}],
        'fixtures': ['toy_arithmetic_bug', 'toy_import_bug'], 'seeds': [0],
        'controls': ['bias'], 'max_steps': 1,
    })
    report = await run_benchmark(config, tmp_path)
    assert len(report['cases']) == 2
    assert not any(case['metrics']['verified_completion'] for case in report['cases'])
    assert report['summary']['fsm']['success_rate'] == 0.
    paths = [case['repository'] for case in report['cases']]
    assert len(set(paths)) == 2
    assert all(case['controller']['latency_ms'] > 0 for case in report['cases'])
    saved = json.loads((tmp_path / report['experiment_id'] / 'report.json').read_text())
    assert saved == report
    second = await run_benchmark(config, tmp_path)
    assert second['experiment_id'] != report['experiment_id']
    assert report['comparison_scope']['matched_compute'] is False
    assert report['comparison_scope']['cortex'] == 'heuristic'


def test_probe_retains_original_candidate_and_preserves_completion():
    from flycode.domain import Observation
    from flycode.llm import HeuristicCandidateGenerator
    from flycode.llm.protocol import CortexContext
    from flycode.research.tournament import TournamentCortex

    context = CortexContext(task='fix', behavior_mode='execute', observation=Observation(task='fix'))
    normal = HeuristicCandidateGenerator().generate_candidates(context)
    proposed = TournamentCortex().generate_candidates(context)
    assert all(any(c.tool == item.tool and c.arguments == item.arguments for c in proposed) for item in normal)
    assert 'git_diff' in {c.tool for c in proposed}
    assert len(proposed) <= 4
    context.behavior_mode = 'consolidate'
    context.observation.tests = {'status': 'passed'}
    assert [c.tool for c in TournamentCortex().generate_candidates(context)] == ['stop']


async def test_new_task_setup_failure_remains_in_report_with_successful_case(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark

    config = BenchmarkConfig.model_validate({
        'controllers': [{'id': 'missing', 'brain': 'functional', 'checkpoint': str(tmp_path / 'missing')},
                        {'id': 'active', 'brain': 'functional'}],
        'fixtures': ['boundary_regression_bug'], 'seeds': [0], 'controls': ['bias'], 'max_steps': 1})
    report = await run_benchmark(config, tmp_path / 'experiment')
    assert len(report['cases']) == 2
    assert report['summary']['missing']['errors'] == 1
    assert report['summary']['missing']['success_rate'] == 0.
    assert report['cases'][1]['error'] is None
    assert report['cases'][1]['fixture_split'] == 'heldout_synthetic'


def test_local_model_budgets_are_validated_before_creating_experiment():
    from flycode.research.benchmark import BenchmarkConfig

    valid = BenchmarkConfig(cortex='lmstudio', max_model_calls=4, request_timeout=20.)
    assert valid.model == 'qwen/qwen3.5-9b'
    for changes in [{'max_model_calls': 0}, {'request_timeout': float('nan')},
                    {'model': 'same', 'decision_model': 'same'}]:
        with pytest.raises(ValueError):
            BenchmarkConfig.model_validate({'cortex': 'lmstudio', **changes})


async def test_benchmark_executes_lesioned_controller_without_changing_source(tmp_path):
    import numpy as np

    from flycode.brain.full_connectome.graph import SparseConnectome, graph_from_edges
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    fingerprint = SparseConnectome.load(graph).fingerprint
    config = BenchmarkConfig.model_validate({
        'controllers': [{'id': 'lesioned', 'brain': 'full', 'connectome_path': str(graph), 'lesion_fraction': 1.}],
        'fixtures': ['toy_arithmetic_bug'], 'seeds': [0], 'controls': ['bias'], 'max_steps': 1})
    report = await run_benchmark(config, tmp_path / 'experiment')
    case = report['cases'][0]
    assert case['error'] is None
    assert case['metrics']['tool_calls'] == 1
    assert case['perturbation']['active_edges'] == 0
    assert case['perturbation']['structural_edges'] == 64
    assert case['perturbation']['parent_fingerprint'] == fingerprint
    assert SparseConnectome.load(graph).fingerprint == fingerprint


async def test_isolated_benchmark_records_requested_bounded_influence(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark

    config = BenchmarkConfig.model_validate({'controllers': [{'id': 'bounded', 'brain': 'functional',
        'observer': {'brain': 'mock'}, 'influence': .1}], 'fixtures': ['toy_arithmetic_bug'],
        'seeds': [0], 'controls': ['bias'], 'max_steps': 1})
    report = await run_benchmark(config, tmp_path)
    case = report['cases'][0]
    assert case['error'] is None
    assert case['metrics']['rollout_interventions'] == 0  # Initial run_tests cannot be displaced.
    record = json.loads((tmp_path / report['experiment_id'] / 'trajectories' /
                         (case['run_id'] + '.steps.jsonl')).read_text())
    assert record['rollout_decision']['requested_influence'] == .1
    assert record['rollout_decision']['applied_influence'] == 0.


def test_influence_configuration_requires_observer_and_supported_level():
    from flycode.research.benchmark import ControllerSpec

    for values in [{'influence': .1}, {'influence': .2, 'observer': {'brain': 'mock'}}]:
        with pytest.raises(ValueError):
            ControllerSpec.model_validate({'id': 'bad', 'brain': 'functional', **values})
