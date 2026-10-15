"""Actual tool execution follows a bounded choice and stops influence after failure."""

import pytest

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.brain.mock import MockBrainBackend
from flycode.domain import CandidateAction, TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.telemetry import TelemetryLogger


class ReadPairCortex(HeuristicCandidateGenerator):
    def __init__(self, alternative='mathutils.py'):
        self.alternative = alternative

    def generate_candidates(self, context):
        if context.observation.step_number == 0:
            return [CandidateAction(id='a', tool='read_file', arguments={'path': 'README.md'}, rank_score=.5, expected_risk=0.),
                    CandidateAction(id='b', tool='read_file', arguments={'path': self.alternative}, rank_score=.49, expected_risk=0.)]
        return super().generate_candidates(context)


class PreferAlternative(MockBrainBackend):
    def score_candidates(self, candidates, mode):
        return {candidate.id: {'a': -1., 'b': 1.}.get(candidate.id, 0.) for candidate in candidates}


@pytest.mark.parametrize('alternative', ['mathutils.py', 'missing.py'])
async def test_real_tool_choice_and_outcome_are_recorded_with_optional_rollback(tmp_path, alternative):
    from flycode.brain.shadow import ShadowBackend

    brain = ShadowBackend(make_brain('functional', seed=0), PreferAlternative(), influence=.1)
    events = []
    telemetry = TelemetryLogger(tmp_path / 'telemetry')
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), brain, ReadPairCortex(alternative),
        TaskSpec(id='rollout', task='fix', fixture='toy_arithmetic_bug'), telemetry=telemetry,
        on_event=lambda kind, data: events.append((kind, data)))
    assert result.success, result.transcript
    steps = telemetry.read_steps('run')
    assert steps[0].chosen_action['arguments']['path'] == alternative
    decision = steps[0].rollout_decision
    assert decision['active_choice'] == 'a'
    assert decision['executed_choice'] == 'b'
    assert decision['intervened']
    assert steps[0].shadow_decision['active_choice'] == 'a'
    assert steps[0].shadow_decision['executed_choice'] == 'b'
    assert result.metrics.rollout_interventions >= 1
    assert any(kind == 'rollout' and data['intervened'] for kind, data in events)
    if alternative == 'missing.py':
        assert decision['rolled_back']
        assert result.metrics.rollout_rollbacks == 1
        assert brain.rollout.state.disabled
        assert all(step.rollout_decision['applied_influence'] == 0. for step in steps[1:])
    else:
        assert not decision['rolled_back']
        assert result.metrics.rollout_rollbacks == 0


@pytest.mark.parametrize('error', [ValueError, TypeError, 'nonfinite', 'missingmap'])
async def test_observer_failure_preserves_active_execution_with_influence_requested(tmp_path, error):
    from flycode.brain.shadow import ShadowBackend

    class FailedObserver(PreferAlternative):
        def step(self, stimulus):
            if isinstance(error, type):
                raise error('observer failure')
            return super().step(stimulus)

        def score_candidates(self, candidates, mode):
            if error == 'nonfinite':
                return {'a': float('nan'), 'b': 1.}
            if error == 'missingmap':
                return None
            return super().score_candidates(candidates, mode)

    brain = ShadowBackend(make_brain('functional', seed=0), FailedObserver(), influence=.1)
    telemetry = TelemetryLogger(tmp_path / 'telemetry')
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), brain, ReadPairCortex(),
        TaskSpec(id='fallback', task='fix', fixture='toy_arithmetic_bug'), telemetry=telemetry)
    assert result.success
    assert telemetry.read_steps('run')[0].chosen_action['arguments']['path'] == 'README.md'
    assert telemetry.read_steps('run')[0].shadow_decision['error']
    assert result.metrics.rollout_interventions == 0
