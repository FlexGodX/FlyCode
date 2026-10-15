"""Legacy random-projection experiment; not a biological connectome.

A projection backend retained for comparison. Use ``--brain full`` for real
biological adjacency. No internal weights are trained - the stimulus-to-state
projection, the state-to-action readout and the per-node oscillation phases
are drawn once (seeded) and then frozen.

The state update is a gain-controlled phase code:

    drive       = W_stimulus @ (s / ||s||)       # divisive gain control
    level_phase = (pi / ln 10) * ln(||s||)       # pi advance per input decade
    latent      = sin(drive + node_phase + level_phase)

Divisive normalization keeps the drive sensitive to the *direction* of the
stimulus, while the level phase re-encodes its absolute *intensity* into the
oscillation phase, so states decorrelate under pure gain changes.

This backend serves two purposes:
1. Research substrate: virtual stimulation/lesion experiments (Phase 5+).
2. Production candidate: benchmark whether a frozen connectome suffices.
"""

import struct
import zlib
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ...domain import NeuralState
from ..protocol import BrainBackend, NeuralStimulus

PHASE_PER_DECADE = float(np.pi / np.log(10.0))
_NORM_EPS = 1e-12


class ConnectomeGraph(BaseModel):
    """Static connectome topology."""

    node_count: int
    edge_list: list[tuple[int, int, float]]  # (from_node, to_node, weight)
    node_types: list[Literal["E", "M", "P"]]  # E=excitatory, M=hub, P=pathway


class FrozenFullConnectomeState(NeuralState):
    """NeuralState extended with the frozen connectome's node-level activity."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    #: (n_state_units, n_stimulus_input) node activation field for this step
    node_states: Any = None

    #: Oscillatory response of every connectome node
    latent_state: list[float] = Field(default_factory=list)


class FrozenFullConnectomeBackend(BrainBackend):
    """
    Frozen Full Connectome Backend (plan §36).

    Uses a static, pre-computed connectome to generate neural states from
    stimuli. The internal structure is frozen; only the readout (state → action)
    is trained.

    This backend is intentionally slow and heavy (~1k–20k neurons after reduction,
    or full ~100k for research). It is NOT suitable for production use by default.
    """

    name = "FrozenFullConnectome"
    tier = 3

    def __init__(
        self,
        n_stimulus_input: int = 64,
        n_state_units: int = 512,
        seed: int | None = None,
    ) -> None:
        super().__init__(seed=seed)
        self.n_stimulus_input = n_stimulus_input
        self.n_state_units = n_state_units

        rng = np.random.default_rng(seed)
        self._stimulus_to_state = rng.standard_normal((n_state_units, n_stimulus_input))
        self._state_to_action = rng.standard_normal((n_stimulus_input, n_state_units))
        self._node_phase = rng.uniform(0.0, 2.0 * np.pi, n_state_units)

        # Optional: load from a binary blob staged via set_connectome()
        self._connectome_data: bytes | None = None

    # ------------------------------------------------ matched-compute profile
    @property
    def memory_usage_mb(self) -> float:
        return (self._stimulus_to_state.nbytes + self._state_to_action.nbytes) / (1024 * 1024)

    @property
    def latency_ms(self) -> float:
        return (self._stimulus_to_state.size + self._state_to_action.size) / 1e6

    # -------------------------------------------------------- connectome I/O
    def set_connectome(self, data: bytes | Path) -> None:
        """Stage a pre-computed connectome blob (see `load_connectome`)."""
        if isinstance(data, Path):
            data = data.read_bytes()
        self._connectome_data = data

    def load_connectome(self) -> bool:
        """Replace the random stimulus->state projection with the staged blob.

        Blob format (zlib-compressed): two little-endian uint32
        (rows=n_state_units, cols=n_stimulus_input) followed by rows*cols
        float32 weights. Returns True if the blob was valid and applied.
        """
        if self._connectome_data is None:
            return False
        raw = zlib.decompress(self._connectome_data)
        rows, cols = struct.unpack("<II", raw[:8])
        weights = np.frombuffer(raw[8:], dtype=np.float32)
        if rows != self.n_state_units or cols != self.n_stimulus_input:
            return False
        if weights.size != rows * cols:
            return False
        self._stimulus_to_state = weights.reshape(rows, cols).astype(np.float64)
        return True

    # -------------------------------------------------------- brain contract
    def reset(self) -> None:
        """Reset the recurrent state; the frozen weights stay untouched."""
        self._state = NeuralState()

    def step(self, stimulus: NeuralStimulus) -> FrozenFullConnectomeState:
        """Step the connectome forward with the given stimulus."""
        s = self._input_vector(stimulus)
        norm = float(np.linalg.norm(s))
        if norm > _NORM_EPS:
            drive = self._stimulus_to_state @ (s / norm)
            level_phase = PHASE_PER_DECADE * float(np.log(norm))
        else:
            drive = np.zeros(self.n_state_units)
            level_phase = 0.0

        latent = np.sin(drive + self._node_phase + level_phase)

        scores = self._state_to_action @ latent
        centred = scores - scores.mean()
        peak = float(np.abs(centred).max())
        if peak > 0.0:
            centred = centred / peak
        action_bias = {f"action_{i}": float(v) for i, v in enumerate(centred)}

        state = FrozenFullConnectomeState(
            state_vector=latent.tolist(),
            latent_state=latent.tolist(),
            action_bias=action_bias,
            behavior_mode="explore",
            update_weight=0.0,
            node_states=np.outer(latent, s),
        )
        self._state = state
        self._steps += 1
        return state

    def get_state(self) -> NeuralState:
        """Return the most recent state without advancing."""
        return self._state

    def close(self) -> None:
        """Release the staged connectome blob."""
        self._connectome_data = None

    # -------------------------------------------------------------- reporting
    def get_memory_capacity(self) -> int:
        """Memory capacity in state units."""
        return self.n_state_units

    def get_computation_cost(self) -> float:
        """Relative cost estimate for matched-compute reporting."""
        return float(self.n_state_units)

    def parameter_count(self) -> int:
        """Frozen weights: the stimulus->state projection is the whole model."""
        return int(self._stimulus_to_state.size)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "steps": self._steps,
            "state_dim": self.n_state_units,
            "params": self.parameter_count(),
            "memory_capacity": self.get_memory_capacity(),
            "cost": self.get_computation_cost(),
            "learning": self._learning_enabled,
            "memory_usage_mb": self.memory_usage_mb,
            "latency_ms": self.latency_ms,
        }

    # --------------------------------------------------------------- internal
    def _input_vector(self, stimulus: NeuralStimulus) -> np.ndarray:
        """Pad/truncate the stimulus embedding to `n_stimulus_input`.

        Without an explicit embedding a stable vector is derived from the
        observation content (crc32 -> seeded RNG), so the backend stays
        deterministic for a given seed and observation.
        """
        embedding = getattr(stimulus, "embedding", None)
        if embedding:
            s = np.asarray(embedding, dtype=np.float64)
        else:
            digest = zlib.crc32(repr(stimulus.observation).encode("utf-8"))
            s = np.random.default_rng(digest).standard_normal(self.n_stimulus_input)
        if s.shape[0] < self.n_stimulus_input:
            s = np.pad(s, (0, self.n_stimulus_input - s.shape[0]))
        return s[: self.n_stimulus_input]
