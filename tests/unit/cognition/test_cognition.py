"""Unit tests for the Functional Fly's cognition modules (plan §10-22)."""

from flycode.cognition import (
    DriveModule,
    HabituationModule,
    MushroomBody,
    NoveltyModule,
    RPEModule,
    TaskCompass,
    ValenceModule,
)
from flycode.domain import Observation, ToolAction


def failing_obs(step=0, errors=None):
    return Observation(
        task="x",
        step_number=step,
        tests={"status": "failed", "failed": 1},
        recent_errors=errors or [],
    )


def passing_obs(step=0):
    return Observation(task="x", step_number=step, tests={"status": "passed"})


class TestTaskCompass:
    def test_repeated_execute_raises_correction_pressure(self):
        compass = TaskCompass(seed=0)
        for _ in range(5):
            compass.update("execute")
        assert compass.state.correction_pressure() > 0
        assert "verify" in compass.correction_bias()

    def test_verify_resets_reps_without_verify(self):
        compass = TaskCompass(seed=0)
        for _ in range(3):
            compass.update("execute")
        assert compass.state.reps_without_verify == 3
        compass.update("verify")
        assert compass.state.reps_without_verify == 0


class TestMushroomBody:
    def test_sparsity_within_target_range(self):
        mb = MushroomBody(seed=0)
        code = mb.encode(failing_obs())
        assert 0.02 <= code.sparsity <= 0.10

    def test_learning_increases_value_toward_reward(self):
        mb = MushroomBody(seed=0)
        code = mb.encode(failing_obs())
        before = mb.value(code, "run_tests")
        for _ in range(20):
            mb.learn(code, "run_tests", reward=1.0)
        after = mb.value(code, "run_tests")
        assert after > before

    def test_recall_best_picks_highest_value_action(self):
        mb = MushroomBody(seed=0)
        code = mb.encode(failing_obs())
        for _ in range(20):
            mb.learn(code, "edit_file", reward=1.0)
            mb.learn(code, "run_tests", reward=-1.0)
        best, _ = mb.recall_best(code, ["edit_file", "run_tests"])
        assert best == "edit_file"


class TestValenceModule:
    def test_fixing_tests_is_positive_utility(self):
        valence = ValenceModule()
        action = ToolAction(id="1", tool="edit_file", arguments={"path": "a.py"})
        signals = valence.evaluate(action, failing_obs(), passing_obs())
        assert signals.utility > 0

    def test_breaking_tests_is_negative_utility_with_aversion(self):
        valence = ValenceModule()
        action = ToolAction(id="1", tool="edit_file", arguments={"path": "a.py"})
        signals = valence.evaluate(action, passing_obs(), failing_obs())
        assert signals.utility < 0
        assert signals.aversion > 0


class TestNoveltyModule:
    def test_first_observation_is_novel(self):
        novelty = NoveltyModule()
        state = novelty.evaluate(failing_obs(errors=["boom"]))
        assert state.novelty > 0

    def test_repeated_observation_is_not_novel(self):
        novelty = NoveltyModule()
        obs = failing_obs(errors=["boom"])
        novelty.evaluate(obs)
        state = novelty.evaluate(obs)
        assert state.novelty == 0.0


class TestRPEModule:
    def test_repeated_predictable_outcome_shrinks_delta(self):
        rpe = RPEModule()
        first = rpe.evaluate("s0", 0.5, "s1")
        for _ in range(20):
            last = rpe.evaluate("s0", 0.5, "s1")
        assert abs(last.delta) < abs(first.delta)


class TestDriveModule:
    def test_stagnation_rises_without_progress(self):
        drives = DriveModule(window=4)
        state = None
        for step in range(6):
            state = drives.update(failing_obs(step))
        assert state.stagnation > 0

    def test_progress_high_when_tests_pass(self):
        drives = DriveModule()
        state = drives.update(passing_obs())
        assert state.progress == 1.0


class TestHabituationModule:
    def test_discount_shrinks_after_useless_repeats(self):
        habituation = HabituationModule()
        action = ToolAction(id="1", tool="run_tests")
        for _ in range(3):
            habituation.record(action, "diagnose", utility=0.0, success=False)
        discount = habituation.discount(action, "diagnose")
        assert discount < 1.0

    def test_no_discount_for_successful_repeats(self):
        habituation = HabituationModule()
        action = ToolAction(id="1", tool="run_tests")
        habituation.record(action, "diagnose", utility=0.5, success=True)
        habituation.record(action, "diagnose", utility=0.5, success=True)
        assert habituation.discount(action, "diagnose") == 1.0
