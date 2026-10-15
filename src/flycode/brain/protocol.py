"""Unified brain backend interface (plan §5).

Every controller - baselines, functional fly, reduced connectome, full
connectome - implements the same interface so they can be swapped by the
CLI (`--brain ...`) and compared on matched compute.
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from ..domain import NeuralState, NeuralStimulus


class BrainBackend(ABC):
    """Base class for all brain backends.

    Required: reset / step / get_state / close.
    Optional: policy bias, learning control, cost accounting.
    """

    #: Human readable name used in reports
    name: str = "brain"

    #: Rough tier: 0 = baseline, 1 = functional fly, 2 = reduced, 3 = connectome
    tier: int = 0

    #: Memory usage in MB (for matched-compute benchmarking)
    memory_usage_mb: float = 0.0

    #: Latency per step in ms (for matched-compute benchmarking)
    latency_ms: float = 0.0

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed
        self._state = NeuralState()
        self._steps = 0
        self._learning_enabled = False

    # ------------------------------------------------------------ required
    @abstractmethod
    def reset(self) -> None:
        """Reset the brain to its initial state."""

    @abstractmethod
    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        """Advance the controller by one step and return its state."""

    @abstractmethod
    def get_state(self) -> NeuralState:
        """Return the most recent state without advancing."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""

    # ------------------------------------------------------------ optional
    def get_policy_bias(self) -> dict[str, float]:
        """Per-tool bias in [-1, 1] produced by this controller."""
        return dict(self._state.action_bias)

    def get_behavior_mode(self) -> str:
        return self._state.behavior_mode

    def set_learning_mode(self, enabled: bool) -> None:
        """Enable or disable bounded online learning."""
        self._learning_enabled = enabled

    def learning_enabled(self) -> bool:
        return self._learning_enabled

    def get_memory_capacity(self) -> int:
        return 0

    def get_computation_cost(self) -> float:
        """Relative cost estimate for matched-compute reporting."""
        return float(self._state.dimensions)

    def parameter_count(self) -> int:
        """Trainable/total parameter count for matched-compute comparisons."""
        return self._state.dimensions

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "steps": self._steps,
            "state_dim": self._state.dimensions,
            "params": self.parameter_count(),
            "memory_capacity": self.get_memory_capacity(),
            "cost": self.get_computation_cost(),
            "learning": self._learning_enabled,
            "latency_ms": self.latency_ms,
        }

    def __enter__(self) -> "BrainBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@runtime_checkable
class BrainProtocol(Protocol):
    """Structural protocol used for duck-typed backend checks."""

    name: str

    def reset(self) -> None: ...

    def step(self, stimulus: NeuralStimulus) -> NeuralState: ...

    def get_state(self) -> NeuralState: ...

    def close(self) -> None: ...


def make_stimulus(
    observation: Any,
    *,
    behavior_mode: str = "explore",
    target_behavior: str = "execute",
    candidates: list[dict[str, Any]] | None = None,
    urgency: float = 0.5,
) -> NeuralStimulus:
    """Helper to build a stimulus from an observation."""
    return NeuralStimulus(
        observation=observation,
        target_behavior=target_behavior,
        behavior_mode=behavior_mode,
        candidates=candidates or [],
        urgency=urgency,
    )