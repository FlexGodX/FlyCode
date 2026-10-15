"""Bounded influence changes only admissible diagnostic choices and latches rollback."""

import pytest

from flycode.domain import CandidateAction, Observation, ToolAction, Transition
from flycode.policy import ControlMode, run_tournament


def candidates(tool='read_file', risk=0.):
    return [CandidateAction(id='a', tool=tool, arguments={'path': 'first.py'}, rank_score=1. if risk else .5, expected_risk=risk),
            CandidateAction(id='b', tool='read_file', arguments={'path': 'second.py'}, rank_score=.49, expected_risk=0.)]


@pytest.mark.parametrize(('influence', 'bound'), [(.1, .02), (.25, .05), (.5, .1), (1., .2)])
def test_diagnostic_reranking_is_bounded_even_for_large_observer_scores(influence, bound):
    from flycode.policy.rollout import RolloutController

    actions = candidates()
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(influence)
    result = controller.select(actions, active, {'a': -1e100, 'b': 1e100})
    assert active.winner.id == 'a'
    assert result.winner.id == 'b'
    assert controller.last.intervened
    assert controller.last.applied_influence == influence
    assert max(abs(value) for value in controller.last.corrections.values()) == pytest.approx(bound)


@pytest.mark.parametrize('tool', ['edit_file', 'create_file', 'stop', 'postgres_query', 'run_tests',
                                  'docker_compose', 'alembic', 'http_request'])
def test_observer_cannot_displace_an_active_nondiagnostic_action(tool):
    from flycode.policy.rollout import RolloutController

    actions = candidates(tool=tool)
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(1.)
    assert controller.select(actions, active, {'a': -1., 'b': 1.}).winner.id == 'a'
    assert controller.last.applied_influence == 0.


@pytest.mark.parametrize('observer', [{'a': float('nan')}, {'b': float('inf')}, {'a': 'invalid'}])
def test_invalid_scores_preserve_the_active_choice(observer):
    from flycode.policy.rollout import RolloutController

    actions = candidates()
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(.5)
    assert controller.select(actions, active, observer).winner.id == 'a'
    assert controller.last.applied_influence == 0.


def test_zero_influence_duplicate_ids_and_risky_active_choice_do_not_intervene():
    from flycode.policy.rollout import RolloutController

    for influence, actions in [(0., candidates()), (.5, candidates(risk=.4)),
                               (.5, [c.model_copy(update={'id': 'same'}) for c in candidates()])]:
        active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
        controller = RolloutController(influence)
        assert controller.select(actions, active, {'a': -1., 'b': 1.}).winner == active.winner
        assert controller.last.applied_influence == 0.


def test_failed_intervened_action_disables_further_influence_within_the_task():
    from flycode.policy.rollout import RolloutController

    actions = candidates()
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(.1)
    assert controller.select(actions, active, {'a': -1., 'b': 1.}).winner.id == 'b'
    obs = Observation(task='diagnostic')
    controller.observe(Transition(previous=obs, current=obs, action=ToolAction(id='b', tool='read_file'), success=False))
    assert controller.state.disabled
    assert controller.state.interventions == 1
    assert controller.state.rollbacks == 1
    assert controller.last.rolled_back
    assert controller.select(actions, active, {'a': -1., 'b': 1.}).winner.id == 'a'


def test_expected_failed_active_tests_do_not_trigger_rollback():
    from flycode.policy.rollout import RolloutController

    actions = candidates(tool='run_tests')
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(.1)
    controller.select(actions, active, {'a': -1., 'b': 1.})
    obs = Observation(task='discovery')
    controller.observe(Transition(previous=obs, current=obs, action=ToolAction(id='a', tool='run_tests'), success=False))
    assert not controller.state.disabled
    assert controller.state.interventions == 0


def test_safety_blocked_alternative_never_receives_influence():
    from flycode.policy.rollout import RolloutController

    actions = candidates()
    actions[1].arguments = {'path': 'secret.txt'}
    active = run_tournament(actions, {}, ControlMode.BIAS_CONTROL)
    controller = RolloutController(1.)
    assert controller.select(actions, active, {'a': -1., 'b': 1.}).winner.id == 'a'
    assert controller.last.applied_influence == 0.


@pytest.mark.parametrize('influence', [-.1, .2, 1.1, float('nan'), float('inf')])
def test_unconfigured_influence_levels_are_rejected(influence):
    from flycode.policy.rollout import RolloutController

    with pytest.raises(ValueError):
        RolloutController(influence)
