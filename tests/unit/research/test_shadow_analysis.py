"""Shadow evidence counts failures and cannot turn recommendations into causal success."""

import json

import numpy as np

from flycode.telemetry import TrainingStep


async def test_benchmark_observer_records_actual_decisions_and_artifact_identity(tmp_path):
    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.research.shadow import analyze_shadow

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    config = BenchmarkConfig.model_validate({'controllers': [{'id': 'shadow', 'brain': 'functional',
        'observer': {'brain': 'full', 'connectome_path': str(graph)}}],
        'fixtures': ['toy_arithmetic_bug', 'toy_import_bug'], 'seeds': [0], 'controls': ['bias']})
    report = await run_benchmark(config, tmp_path / 'experiment')
    assert report['summary']['shadow']['success_rate'] == 1.
    analysis = analyze_shadow(tmp_path / 'experiment' / report['experiment_id'] / 'trajectories')
    assert analysis['attempted_decisions'] == 18
    assert analysis['available_recommendations'] == 18
    assert analysis['coverage'] == 1.
    assert analysis['observer_errors'] == 0
    assert analysis['counterfactual_success'] is None
    assert report['source_unchanged'] is True
    assert len(report['implementation_sha256']) == 64
    identity = report['cases'][0]['identity']
    assert identity['observer']['graph_fingerprint']
    assert identity['active']['brain'] == 'functional'


def test_analysis_preserves_missing_recommendations_legacy_and_invalid_record_counts(tmp_path):
    from flycode.research.shadow import analyze_shadow

    base = TrainingStep(observation={}, chosen_action={'id': 'a', 'tool': 'read_file'}, outcome={})
    success = {'influence': 0., 'active_choice': 'a', 'shadow_choice': 'a', 'agreement': True,
               'risk_delta': 0., 'observer_latency_ms': 2., 'error': None}
    failure = {**success, 'shadow_choice': None, 'agreement': None, 'risk_delta': None,
               'observer_latency_ms': 1., 'error': 'observer failed'}
    records = [base.model_dump_json(),
        base.model_copy(update={'shadow_decision': success}).model_dump_json(),
        base.model_copy(update={'shadow_decision': failure}).model_dump_json(), '{broken']
    (tmp_path / 'run.steps.jsonl').write_text('\n'.join(records))
    analysis = analyze_shadow(tmp_path)
    assert analysis['legacy_steps'] == 1
    assert analysis['invalid_records'] == 1
    assert analysis['attempted_decisions'] == 2
    assert analysis['available_recommendations'] == 1
    assert analysis['coverage'] == .5
    assert analysis['agreement_rate'] == 1.
    assert analysis['observer_errors'] == 1
    assert analysis['mean_observer_latency_ms'] == 1.5


def test_analysis_rejects_inconsistent_agreement_and_nonfinite_latency(tmp_path):
    from flycode.research.shadow import analyze_shadow

    trace = {'influence': 0., 'active_choice': 'a', 'shadow_choice': 'b', 'agreement': True,
             'risk_delta': .1, 'observer_latency_ms': 1., 'error': None}
    rows = [TrainingStep(observation={}, chosen_action={}, outcome={}, shadow_decision=trace).model_dump_json()]
    trace['agreement'] = False
    trace['observer_latency_ms'] = float('inf')
    rows.append(json.dumps({'observation': {}, 'chosen_action': {}, 'outcome': {}, 'shadow_decision': trace}))
    (tmp_path / 'run.steps.jsonl').write_text('\n'.join(rows))
    report = analyze_shadow(tmp_path)
    assert report['invalid_records'] == 2
    assert report['available_recommendations'] == 0
