"""Repeated episodes isolate learned controller state from repositories/cortex memory."""

import json

import pytest


async def test_adaptation_retains_only_learning_condition_policy_between_fresh_tasks(tmp_path):
    from flycode.research.adaptation import AdaptationConfig, run_adaptation

    config = AdaptationConfig(seeds=[0], fixtures=['toy_arithmetic_bug'], repeats=2)
    report = await run_adaptation(config, tmp_path)
    assert len(report['cases']) == 4
    assert all(case['metrics']['verified_completion'] for case in report['cases'])
    assert len({case['repository'] for case in report['cases']}) == 4
    frozen = [case for case in report['cases'] if not case['learning']]
    learned = [case for case in report['cases'] if case['learning']]
    assert all(case['policy_before'] == case['policy_after'] for case in frozen)
    assert learned[1]['policy_before'] == learned[0]['policy_after']
    assert learned[0]['policy_before'] == frozen[0]['policy_before']
    assert learned[0]['policy_after'] != learned[0]['policy_before']
    assert sum(case['metrics']['online_updates'] for case in learned) > 0
    assert all(case['guard']['max_applied_norm'] <= .5 for case in learned)
    assert report['faster_adaptation_demonstrated'] is False  # Both conditions actually take 9 steps.
    saved = json.loads((tmp_path / report['experiment_id'] / 'report.json').read_text())
    assert saved == report


async def test_adaptation_records_checkpoint_failure_for_every_planned_episode(tmp_path):
    from flycode.research.adaptation import AdaptationConfig, run_adaptation

    config = AdaptationConfig(seeds=[0], fixtures=['toy_import_bug'], repeats=2, checkpoint=tmp_path / 'missing')
    report = await run_adaptation(config, tmp_path / 'experiment')
    assert len(report['cases']) == 4
    assert all(case['error'] and case['metrics'] is None for case in report['cases'])
    assert report['summary']['learning']['success_rate'] == 0.
    assert report['summary']['frozen']['errors'] == 2


def test_adaptation_rejects_noncomparable_or_invalid_sequences():
    from flycode.research.adaptation import AdaptationConfig

    for change in [{'repeats': 1}, {'fixtures': []}, {'fixtures': ['toy_import_bug', 'toy_import_bug']},
                   {'seeds': [0, 0]}, {'seeds': []}, {'max_steps': 0}]:
        with pytest.raises(ValueError):
            AdaptationConfig.model_validate(change)
