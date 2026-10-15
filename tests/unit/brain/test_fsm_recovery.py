"""Failed verification and regressions enter recovery without crashing the baseline."""

from flycode.brain import make_brain, make_stimulus
from flycode.domain import Observation


def test_failed_verification_recovers_then_reaches_verified_stop():
    brain = make_brain('heuristic_fsm')
    failed = make_stimulus(Observation(task='recover', tests={'status': 'failed', 'failed': 1}))
    for _ in range(9):
        state = brain.step(failed)
    assert state.behavior_mode == 'recover'
    passed = make_stimulus(Observation(task='recover', tests={'status': 'passed'}))
    assert brain.step(passed).behavior_mode == 'verify'
    assert brain.step(passed).behavior_mode == 'consolidate'
    assert brain.step(passed).behavior_mode == 'stop'


def test_regression_enters_recovery_from_any_workflow_stage():
    brain = make_brain('heuristic_fsm')
    brain.step(make_stimulus(Observation(task='regression', tests={'status': 'passed'})))
    state = brain.step(make_stimulus(Observation(task='regression', tests={'status': 'failed', 'failed': 1})))
    assert state.behavior_mode == 'recover'
    assert brain.recoveries == 1
