"""Immutable-input repetition must not trap candidate arbitration (plan §21)."""

import pytest

from flycode.brain import FunctionalFlyBackend
from flycode.cognition import HabituationModule
from flycode.domain import CandidateAction
from flycode.policy import ControlMode, run_tournament


def read(candidate_id='a', artifact='first', path='src/example.py'):
    arguments = {'path': path}
    if artifact is not None:
        arguments['artifact_id'] = artifact
    return CandidateAction(id=candidate_id, tool='read_file', arguments=arguments)


def test_pinned_reads_do_not_share_repetition_with_other_inputs():
    history = HabituationModule()
    first = read()
    for _ in range(4):
        history.record(first, 'execute', utility=0., success=False)
    assert history.discount(first, 'execute') < 1.
    for candidate in (read('b', 'second'), read('c', None), read('d', 'first', 'other.py')):
        assert history.discount(candidate, 'execute') == 1.
        assert history.repetition_streak(candidate, 'execute') == 0
    assert history.discount(read('new-id'), 'execute') < 1.
    assert history.discount(first, 'verify') == 1.


def test_successful_zero_utility_pin_reads_lose_weight_without_changing_success():
    history = HabituationModule()
    candidate = read()
    first = history.record(candidate, 'execute', utility=0., success=True)
    assert first.successes == 1
    assert history.discount(candidate, 'execute') == 1.
    for _ in range(3):
        entry = history.record(candidate, 'execute', utility=0., success=True)
    assert entry.successes == 4
    assert history.discount(candidate, 'execute') < 1.
    history.reset()
    assert history.discount(candidate, 'execute') == 1.


@pytest.mark.parametrize('bias', [-.5, 0., .5])
@pytest.mark.parametrize('control', [ControlMode.BIAS_CONTROL, ControlMode.HARD_CONTROL])
def test_unread_pin_wins_tie_after_useless_reads_even_with_signed_bias(bias, control):
    brain = FunctionalFlyBackend(seed=0)
    brain.get_state().action_bias['read_file'] = bias
    first, second = read(), read('b', 'second')
    candidates = [first, second]
    before = brain.score_candidates(candidates, 'execute')
    assert run_tournament(candidates, before, control).winner.id == 'a'
    for _ in range(3):
        brain.record_outcome(first, 'execute', utility=0., success=True)
    after = brain.score_candidates(candidates, 'execute')
    assert after['a'] < before['a']
    assert after['b'] == before['b']
    assert run_tournament(candidates, after, control).winner.id == 'b'
    brain.close()


@pytest.mark.parametrize('artifact,utility', [('first', .5), (None, 0.)])
def test_useful_pins_and_mutable_workspace_reads_keep_existing_repeat_behavior(artifact, utility):
    history = HabituationModule()
    candidate = read(artifact=artifact)
    for _ in range(4):
        history.record(candidate, 'execute', utility=utility, success=True)
    assert history.discount(candidate, 'execute') == 1.
