"""Contract tests for every BrainBackend (plan §5)."""

import pytest
import numpy as np

from flycode.brain import (
    BRAIN_REGISTRY,
    FunctionalFlyBackend,
    FrozenFullConnectomeBackend,
    GenericRNNBackend,
    HeuristicFSMBackend,
    MockBrainBackend,
    RandomReservoirBackend,
    make_brain,
    make_stimulus,
)
from flycode.domain import NeuralState, Observation


def failing_observation(step: int = 0) -> Observation:
    return Observation(
        task="fix it",
        step_number=step,
        tests={"status": "failed", "failed": 1, "total": 2, "passed": 1},
        recent_errors=["AssertionError: boom"],
    )


def healthy_observation(step: int = 0) -> Observation:
    return Observation(task="fix it", step_number=step, tests={"status": "passed", "passed": 2, "total": 2})


@pytest.mark.parametrize(
    "backend_cls",
    [MockBrainBackend, HeuristicFSMBackend, RandomReservoirBackend, GenericRNNBackend, FunctionalFlyBackend, FrozenFullConnectomeBackend],
)
class TestBrainContract:
    def test_reset_step_close(self, backend_cls):
        backend = backend_cls(seed=0)
        backend.reset()
        stimulus = make_stimulus(failing_observation(), behavior_mode="explore")
        state = backend.step(stimulus)
        assert isinstance(state, NeuralState)
        assert backend.get_state() is state
        backend.close()

    def test_deterministic_given_seed(self, backend_cls):
        obs = failing_observation()
        b1 = backend_cls(seed=42)
        b2 = backend_cls(seed=42)
        s1 = b1.step(make_stimulus(obs, behavior_mode="explore"))
        s2 = b2.step(make_stimulus(obs, behavior_mode="explore"))
        assert s1.state_vector == pytest.approx(s2.state_vector)
        b1.close()
        b2.close()

    def test_describe_reports_matched_compute_fields(self, backend_cls):
        backend = backend_cls(seed=0)
        description = backend.describe()
        assert description["name"] == backend.name
        assert description["params"] >= 0
        backend.close()


def test_make_brain_registry_covers_all_names():
    for name in BRAIN_REGISTRY:
        backend = make_brain(name, seed=0)
        assert backend.name
        backend.close()


def test_make_brain_unknown_raises():
    with pytest.raises(ValueError):
        make_brain("not-a-real-brain")


class TestFunctionalFlyBackend:
    def test_diagnose_mode_when_tests_failing(self):
        backend = FunctionalFlyBackend(seed=0)
        state = backend.step(make_stimulus(failing_observation(), behavior_mode="explore"))
        assert state.behavior_mode in ("diagnose", "explore")

    def test_moves_toward_execute_under_stagnation(self):
        backend = FunctionalFlyBackend(seed=0)
        mode = "explore"
        modes_seen = []
        for step in range(10):
            state = backend.step(make_stimulus(failing_observation(step), behavior_mode=mode))
            mode = state.behavior_mode
            modes_seen.append(mode)
        assert "execute" in modes_seen

    def test_reaches_consolidate_once_healthy(self):
        backend = FunctionalFlyBackend(seed=0)
        mode = "verify"
        for step in range(3):
            state = backend.step(make_stimulus(healthy_observation(step), behavior_mode=mode))
            mode = state.behavior_mode
        assert mode in ("consolidate", "stop")

    def test_score_candidates_and_record_outcome(self):
        from flycode.domain import CandidateAction

        backend = FunctionalFlyBackend(seed=0)
        backend.step(make_stimulus(failing_observation(), behavior_mode="explore"))
        candidates = [
            CandidateAction(id="a", tool="run_tests"),
            CandidateAction(id="b", tool="edit_file", arguments={"path": "x.py"}),
        ]
        scores = backend.score_candidates(candidates, "diagnose")
        assert set(scores) == {"a", "b"}
        backend.record_outcome(candidates[0], "diagnose", utility=0.1, success=True)


class TestFrozenFullConnectomeBackend:
    """Tests for FrozenFullConnectomeBackend."""

    def test_tier_three(self):
        """Frozen connectome is tier 3 (full biological connectome)."""
        brain = FrozenFullConnectomeBackend()
        assert brain.tier == 3

    def test_step_returns_neural_state(self):
        """Step should return a NeuralState."""
        from flycode.domain import NeuralStimulus

        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)
        stimulus = NeuralStimulus(
            observation=Observation(task="fix it"),
            embedding=[0.1] * 8,
            intensity=1.0,
        )
        state = brain.step(stimulus)

        assert isinstance(state, NeuralState)
        assert len(state.state_vector) == 16
        assert state.behavior_mode == "explore"
        brain.close()

    def test_deterministic_given_seed(self):
        """Should be deterministic given seed."""
        obs = failing_observation()
        b1 = FrozenFullConnectomeBackend(seed=42)
        b2 = FrozenFullConnectomeBackend(seed=42)
        state1 = b1.step(make_stimulus(obs, behavior_mode="explore"))
        state2 = b2.step(make_stimulus(obs, behavior_mode="explore"))
        assert state1.state_vector == pytest.approx(state2.state_vector)
        b1.close()
        b2.close()

    def test_memory_usage_scales(self):
        """Memory usage should scale with state dimensions."""
        brain_small = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=64)
        brain_large = FrozenFullConnectomeBackend(n_stimulus_input=256, n_state_units=256)

        assert brain_small.memory_usage_mb < brain_large.memory_usage_mb

    def test_parameter_count_frozen(self):
        """Frozen backend should have parameter count based on weight matrix size."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)
        brain._stimulus_to_state = np.random.randn(32, 16)

        assert brain.parameter_count() == 512  # 32 * 16

    def test_memory_capacity(self):
        """Memory capacity should equal state units."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)

        assert brain.get_memory_capacity() == 32

    def test_get_computation_cost(self):
        """Computation cost should scale with state dimensions."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        cost = brain.get_computation_cost()
        assert cost == 16  # Should return n_state_units

    def test_learning_disabled(self):
        """Frozen backend should have learning disabled."""
        brain = FrozenFullConnectomeBackend()

        assert brain.learning_enabled() == False

    def test_describe(self):
        """Describe should return correct brain metrics."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        desc = brain.describe()
        assert desc["name"] == "FrozenFullConnectome"
        assert desc["tier"] == 3
        assert desc["state_dim"] == 16
        assert desc["params"] > 0
        assert desc["memory_usage_mb"] > 0
        assert desc["latency_ms"] > 0

    def test_memory_usage_scales(self):
        """Memory usage should scale with state dimensions."""
        brain_small = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=64)
        brain_large = FrozenFullConnectomeBackend(n_stimulus_input=256, n_state_units=256)

        assert brain_small.memory_usage_mb < brain_large.memory_usage_mb

    def test_parameter_count_frozen(self):
        """Frozen backend should have parameter count based on weight matrix size."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)
        brain._stimulus_to_state = np.random.randn(32, 16)

        assert brain.parameter_count() == 512  # 32 * 16

    def test_memory_capacity(self):
        """Memory capacity should equal state units."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)

        assert brain.get_memory_capacity() == 32

    def test_get_computation_cost(self):
        """Computation cost should scale with state dimensions."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        cost = brain.get_computation_cost()
        assert cost == 16  # Should return n_state_units

    def test_learning_disabled(self):
        """Frozen backend should have learning disabled."""
        brain = FrozenFullConnectomeBackend()

        assert brain.learning_enabled() == False

    def test_describe(self):
        """Describe should return correct brain metrics."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        desc = brain.describe()
        assert desc["name"] == "FrozenFullConnectome"
        assert desc["tier"] == 3
        assert desc["state_dim"] == 16
        assert desc["params"] > 0
        assert desc["memory_usage_mb"] > 0
        assert desc["latency_ms"] > 0
