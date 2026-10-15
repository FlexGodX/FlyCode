"""Brain backends - unified interface for every controller tier (plan §4-5).

Tiers:
- Tier 0: MockBrainBackend, HeuristicFSMBackend, RandomReservoirBackend, GenericRNNBackend
- Tier 1: FunctionalFlyBackend
- Tier 2: ReducedConnectomeBackend (not yet implemented)
- Tier 3: FrozenFullConnectomeBackend, TrainableConnectomeBackend (not yet implemented)
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
    "BRAIN_REGISTRY",
    "make_brain",
]
