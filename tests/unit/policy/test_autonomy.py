"""Validated hard selection falls back after a failed changed choice, never bypassing safety."""

from flycode.domain import CandidateAction, Observation, ToolAction, Transition
from flycode.policy import ControlMode


def proposals():
    return [CandidateAction(id='read', tool='read_file', rank_score=.95, expected_risk=0.),
            CandidateAction(id='edit', tool='edit_file', rank_score=.05, expected_risk=.1)]


def test_failed_hard_choice_switches_subsequent_selection_to_bias_control():
    from flycode.policy.autonomy import AutonomyController

    guard = AutonomyController()
    actions = proposals()
    assert guard.select(actions, {'read': 0., 'edit': .8}).winner.id == 'edit'
    assert guard.last.bias_choice == 'read'
    obs = Observation(task='guard')
    guard.observe(Transition(previous=obs, current=obs, action=ToolAction(id='edit', tool='edit_file'), success=False))
    assert guard.control_mode is ControlMode.BIAS_CONTROL
    assert guard.rollbacks == 1
    assert guard.last.rolled_back
    assert guard.select(actions, {'read': 0., 'edit': .8}).winner.id == 'read'


def test_expected_test_discovery_failure_does_not_disable_hard_control():
    from flycode.policy.autonomy import AutonomyController

    guard = AutonomyController()
    action = CandidateAction(id='test', tool='run_tests')
    guard.select([action], {'test': 1.})
    obs = Observation(task='discovery')
    guard.observe(Transition(previous=obs, current=obs, action=ToolAction(id='test', tool='run_tests'), success=False))
    assert guard.control_mode is ControlMode.HARD_CONTROL
    assert guard.rollbacks == 0


def test_verified_regression_disables_hard_control_even_when_both_rankings_agree():
    from flycode.policy.autonomy import AutonomyController

    guard = AutonomyController()
    action = CandidateAction(id='test', tool='run_tests')
    guard.select([action], {'test': 1.})
    guard.observe(Transition(previous=Observation(task='regression', tests={'status': 'passed'}),
        current=Observation(task='regression', tests={'status': 'failed', 'failed': 1}),
        action=ToolAction(id='test', tool='run_tests'), success=False, delta={'tests_status_changed': True}))
    assert guard.control_mode is ControlMode.BIAS_CONTROL
    assert guard.rollbacks == 1


def test_hard_ranking_keeps_the_safety_veto():
    from flycode.policy.autonomy import AutonomyController

    guard = AutonomyController()
    actions = proposals() + [CandidateAction(id='drop', tool='postgres_query', arguments={'query': 'DROP TABLE users'})]
    result = guard.select(actions, {'read': 0., 'edit': .8, 'drop': 100.})
    assert result.winner.id == 'edit'
    assert 'drop' in result.blocked
