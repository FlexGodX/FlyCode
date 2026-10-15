"""Learning has finite budgets, stable encoding and verifiable rollback."""

import numpy as np
import pytest

from flycode.brain import make_brain, make_stimulus
from flycode.cognition import MushroomBody, RPEModule
from flycode.domain import Observation, ToolAction, Transition


def observation(step=0):
    return Observation(task='repeat family', task_id='family', step_number=step,
                       tests={'status': 'failed', 'failed': 1})


def transition(tool='read_file', reward=.8):
    return Transition(previous=observation(), action=ToolAction(id='executed', tool=tool),
                      current=observation(1), utility=reward, success=False)


def test_mb_reports_actual_applied_norm_and_caps_rate_and_nonfinite_updates():
    mb = MushroomBody(seed=0, learning_rate=100.)
    code = mb.encode(observation())
    before = mb.value(code, 'read_file')
    applied = mb.learn(code, 'read_file', 1e6)
    assert 0 < applied <= .5
    assert mb.learning_rate <= .1
    actual = np.linalg.norm(mb.credit['read_file'])
    assert applied == pytest.approx(actual)
    assert mb.value(code, 'read_file') > before
    saved = mb.credit['read_file'].copy()
    with pytest.raises(ValueError, match='finite'):
        mb.learn(code, 'read_file', np.nan)
    np.testing.assert_array_equal(mb.credit['read_file'], saved)


def test_rpe_has_finite_rate_value_and_memory_limits():
    rpe = RPEModule(learning_rate=100.)
    assert rpe.learning_rate <= .1
    with pytest.raises(ValueError, match='finite'):
        rpe.evaluate('start', np.inf, 'end')
    assert not rpe._values
    for i in range(1200):
        rpe.evaluate(str(i), 1e100, 'end')
    assert len(rpe._values) <= 1024
    assert max(abs(value) for value in rpe._values.values()) <= 10.


def test_reset_preserves_learned_policy_and_encoding_but_clears_task_state(tmp_path):
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    initial = brain.mushroom_body.encode(observation())
    brain.step(make_stimulus(observation()))
    brain.learn_transition(transition())
    value = brain.mushroom_body.value(initial, 'read_file')
    assert value > 0
    brain.reset()
    assert brain.get_state().behavior_mode == 'explore'
    assert not brain.habituation.recent()
    assert brain.mushroom_body.encode(observation()).active_indices == initial.active_indices
    assert brain.mushroom_body.value(initial, 'read_file') == value
    assert brain.mushroom_body.updates == 1
    brain.save_learning(tmp_path)
    restored = make_brain('functional', seed=0)
    restored.load_learning(tmp_path)
    assert restored.mushroom_body.value(initial, 'read_file') == value
    with pytest.raises(ValueError, match='encoding'):
        make_brain('functional', seed=9).load_learning(tmp_path)
    brain.clear_learning()
    assert brain.mushroom_body.value(initial, 'read_file') == 0.


def test_learning_is_only_triggered_by_executed_transitions_and_can_be_disabled():
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    brain.step(make_stimulus(observation()))
    brain.step(make_stimulus(observation(1)))
    assert brain.mushroom_body.updates == 0
    brain.learn_transition(transition())
    assert brain.mushroom_body.updates == 1
    brain.set_learning_mode(False)
    brain.learn_transition(transition())
    assert brain.mushroom_body.updates == 1


def test_snapshot_is_detached_and_drift_rolls_back_the_proposed_update():
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    snapshot = brain.snapshot_policy()
    brain.adaptation.max_policy_change = 0.
    brain.learn_transition(transition())
    assert brain.adaptation.state.rollbacks == 1
    assert brain.adaptation.state.last_reason == 'policy drift'
    assert brain.adaptation.state.last_applied_norm == 0.
    assert brain.snapshot_policy() == snapshot
    brain.adaptation.max_policy_change = .25
    brain.learn_transition(transition())
    learned = brain.snapshot_policy()
    assert learned != snapshot
    brain.restore_policy(snapshot)
    assert brain.snapshot_policy() == snapshot
    assert learned != snapshot  # The saved snapshot did not mutate on restore.


def test_repeated_family_prediction_improves_and_reversal_is_bounded():
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    code = brain.mushroom_body.encode(observation())
    before = abs(.8 - brain.mushroom_body.value(code, 'read_file'))
    for _ in range(80):
        brain.learn_transition(transition())
        brain.reset()
    after = abs(.8 - brain.mushroom_body.value(code, 'read_file'))
    assert after < before / 4
    for _ in range(160):
        brain.learn_transition(transition(reward=-.8))
        brain.reset()
    assert brain.mushroom_body.value(code, 'read_file') < -.5
    assert brain.adaptation.state.max_applied_norm <= .5
    assert len(brain.adaptation.replay) <= 128


def test_invalid_guard_limits_leave_policy_unchanged():
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    before = brain.snapshot_policy()
    brain.adaptation.max_policy_change = np.nan
    with pytest.raises(ValueError, match='finite'):
        brain.learn_transition(transition())
    assert brain.snapshot_policy() == before


def test_malformed_saved_memory_leaves_policy_unchanged(tmp_path):
    brain = make_brain('functional', seed=0)
    before = brain.snapshot_policy()
    (tmp_path / 'learning.json').write_text('[]')
    with pytest.raises(ValueError):
        brain.load_learning(tmp_path)
    assert brain.snapshot_policy() == before


def test_replay_evicts_oldest_evidence_and_rejects_regression_on_retained_cases():
    from flycode.cognition.adaptation import AdaptationGuard
    from flycode.cognition.mushroom_body import SparseCode

    guard = AdaptationGuard()
    memory = MushroomBody(seed=0, learning_rate=.1)
    memory.credit['read_file'] = np.zeros(256)
    memory.credit['read_file'][0] = 1.6
    for index in range(1, 141):
        guard.remember(SparseCode(active_indices=[0, index], activity=[.5, 0.], n_units=256),
                       'read_file', .8)
    assert len(guard.replay) == 128
    assert guard.replay[0].code.active_indices == [0, 13]
    current = SparseCode(active_indices=[0], activity=[1.], n_units=256)
    guard.remember(current, 'read_file', -.8)
    before = guard.predictions(memory)
    norm = memory.learn(current, 'read_file', -.8)
    assert not guard.accept(memory, before, norm)
    assert guard.state.last_reason == 'replay regression'
    assert guard.state.last_policy_change <= .25


@pytest.mark.parametrize(('module', 'limit'), [('rpe', 'learning_rate'),
    ('mushroom_body', 'learning_rate'), ('mushroom_body', 'max_update_norm')])
def test_invalid_learning_limits_cannot_leave_a_partial_update(module, limit):
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    before = brain.snapshot_policy()
    setattr(getattr(brain, module), limit, np.nan)
    with pytest.raises(ValueError, match='finite'):
        brain.learn_transition(transition())
    assert brain.snapshot_policy() == before
    assert not brain.adaptation.replay


def test_persistent_policy_rejects_different_encoder_sparsity(tmp_path):
    brain = make_brain('functional', seed=0, sparsity=.05)
    brain.save_learning(tmp_path)
    other = make_brain('functional', seed=0, sparsity=.10)
    with pytest.raises(ValueError, match='encoding'):
        other.load_learning(tmp_path)
