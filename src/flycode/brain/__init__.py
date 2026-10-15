"""Brain backends - unified interface for every controller tier (plan §4-5).

Tiers:
- Tier 0: MockBrainBackend, HeuristicFSMBackend, RandomReservoirBackend, GenericRNNBackend
- Tier 1: FunctionalFlyBackend, optionally with a validated distilled readout
- Tier 2: ReducedConnectomeBackend with inherited parent mappings
- Tier 3: FullConnectomeBackend, optionally with offline-constrained parameters

Legacy FrozenFullConnectomeBackend is a random-projection comparison only.
"""

from ..domain import NeuralState, NeuralStimulus
from .functional_fly import FunctionalFlyBackend
from .generic_rnn import GenericRNNBackend
from .heuristic_fsm import HeuristicFSMBackend
from .mock import MockBrainBackend
from .protocol import BrainBackend, BrainProtocol, make_stimulus
from .random_reservoir import RandomReservoirBackend
from .full_connectome.frozen_full_connectome import FrozenFullConnectomeBackend
from .full_connectome.sparse_backend import FullConnectomeBackend
from .reduced_connectome import ReducedConnectomeBackend

BRAIN_REGISTRY: dict[str, type[BrainBackend]] = {
    "mock": MockBrainBackend,
    "heuristic_fsm": HeuristicFSMBackend,
    "random_reservoir": RandomReservoirBackend,
    "generic_rnn": GenericRNNBackend,
    "functional": FunctionalFlyBackend,
    "functional_fly": FunctionalFlyBackend,
    "frozen_connectome": FrozenFullConnectomeBackend,
    "frozen_full_connectome": FrozenFullConnectomeBackend,
    "full": FullConnectomeBackend,
    "reduced": ReducedConnectomeBackend,
}


def make_brain(name: str, seed: int | None = None, **kwargs) -> BrainBackend:
    """Instantiate a brain backend by CLI name (`--brain ...`, plan §47)."""
    try:
        cls = BRAIN_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown brain backend '{name}'. Available: {sorted(BRAIN_REGISTRY)}"
        ) from exc
    return cls(seed=seed, **kwargs)


__all__ = [
    "BrainBackend",
    "BrainProtocol",
    "NeuralState",
    "NeuralStimulus",
    "make_stimulus",
    "MockBrainBackend",
    "HeuristicFSMBackend",
    "RandomReservoirBackend",
    "GenericRNNBackend",
    "FunctionalFlyBackend",
    "FrozenFullConnectomeBackend",
    "FullConnectomeBackend",
    "ReducedConnectomeBackend",
    "BRAIN_REGISTRY",
    "make_brain",
]
