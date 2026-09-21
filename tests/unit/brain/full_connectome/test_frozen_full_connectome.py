"""Tests for FrozenFullConnectomeBackend (plan §36)."""

import pytest
import numpy as np

from flycode.brain.full_connectome.frozen_full_connectome import (
    FrozenFullConnectomeBackend,
    FrozenFullConnectomeState,
    ConnectomeGraph,
)


class TestFrozenFullConnectomeBackend:
    """Tests for FrozenFullConnectomeBackend basic functionality."""

    def test_init_default(self) -> None:
        """Default initialization with random weights."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=64, n_state_units=512)

        assert brain.n_stimulus_input == 64
        assert brain.n_state_units == 512
        assert brain._stimulus_to_state.shape == (512, 64)

    def test_reset_noop(self) -> None:
        """Reset should be a no-op for frozen backend."""
        brain = FrozenFullConnectomeBackend()
        brain.reset()

        # Should not raise and should not modify state
        assert brain._stimulus_to_state.shape == (512, 64)

    def test_step_returns_valid_state(self) -> None:
        """Step should return a valid NeuralState."""
        from flycode.domain import NeuralStimulus, Observation

        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)
        stimulus = NeuralStimulus(
            observation=Observation(task="fix it"),
            embedding=[0.1] * 4 + [0.2] * 4,  # 8-dim stimulus
            intensity=1.0,
        )
        result = brain.step(stimulus)

        assert isinstance(result, FrozenFullConnectomeState)
        assert result.node_states.shape == (16, 8)
        assert len(result.latent_state) == 16
        assert "action_0" in result.action_bias
        assert result.action_bias["action_0"] >= -1.0  # Zero-centered
        assert result.action_bias["action_0"] <= 1.0
        assert result.behavior_mode == "explore"  # Default mode

    def test_step_with_multiple_inputs(self) -> None:
        """Step should handle multiple input features."""
        from flycode.domain import NeuralStimulus, Observation

        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)
        stimulus = NeuralStimulus(
            observation=Observation(task="fix it"),
            embedding=[0.5] * 16,
            intensity=2.0,
        )
        result = brain.step(stimulus)

        assert len(result.latent_state) == 32
        assert len(result.action_bias) == 16

    def test_different_stimuli_produce_different_states(self) -> None:
        """Different stimuli should produce different neural states."""
        from flycode.domain import NeuralStimulus, Observation

        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)
        brain._stimulus_to_state = np.random.randn(32, 16)

        stimulus1 = NeuralStimulus(observation=Observation(task="fix it"), embedding=[0.1] * 16, intensity=1.0)
        stimulus2 = NeuralStimulus(observation=Observation(task="fix it"), embedding=[0.9] * 16, intensity=1.0)

        state1 = brain.step(stimulus1)
        state2 = brain.step(stimulus2)

        # States should be somewhat different (correlation check)
        if len(state1.latent_state) > 0 and len(state2.latent_state) > 0:
            corr = np.corrcoef(
                state1.latent_state[:10],
                state2.latent_state[:10],
            ).mean()
            assert corr < 0.5  # Random chance would be ~0

    def test_action_bias_computation(self) -> None:
        """Action bias should be zero-centered."""
        from flycode.domain import NeuralStimulus, Observation

        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)
        stimulus = NeuralStimulus(observation=Observation(task="fix it"), embedding=[0.5] * 8, intensity=1.0)
        result = brain.step(stimulus)

        values = list(result.action_bias.values())
        assert abs(sum(values)) < 1e-6  # Essentially zero-centered

    def test_name_and_tier(self) -> None:
        """Brain should have correct name and tier."""
        brain = FrozenFullConnectomeBackend()

        assert brain.name == "FrozenFullConnectome"
        assert brain.tier == 3

    def test_memory_usage(self) -> None:
        """Memory usage should scale with state dimensions."""
        brain_small = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=64)
        brain_large = FrozenFullConnectomeBackend(n_stimulus_input=256, n_state_units=256)

        assert brain_small.memory_usage_mb < brain_large.memory_usage_mb

    def test_parameter_count_frozen(self) -> None:
        """Frozen backend should have parameter count based on weight matrix size."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)
        brain._stimulus_to_state = np.random.randn(32, 16)

        assert brain.parameter_count() == 512  # 32 * 16

    def test_memory_capacity(self) -> None:
        """Memory capacity should equal state units."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=16, n_state_units=32)

        assert brain.get_memory_capacity() == 32

    def test_get_computation_cost(self) -> None:
        """Computation cost should scale with state dimensions."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        cost = brain.get_computation_cost()
        assert cost == 16  # Should return n_state_units

    def test_learning_disabled(self) -> None:
        """Frozen backend should have learning disabled."""
        brain = FrozenFullConnectomeBackend()

        assert brain.learning_enabled() == False

    def test_describe(self) -> None:
        """Describe should return correct brain metrics."""
        brain = FrozenFullConnectomeBackend(n_stimulus_input=8, n_state_units=16)

        desc = brain.describe()
        assert desc["name"] == "FrozenFullConnectome"
        assert desc["tier"] == 3
        assert desc["state_dim"] == 16
        assert desc["params"] > 0
        assert desc["memory_usage_mb"] > 0
        assert desc["latency_ms"] > 0
