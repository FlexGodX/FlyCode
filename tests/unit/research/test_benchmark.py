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
