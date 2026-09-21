"""Generic RNN baseline controller with an adaptible readout.

Matched-compute counterpart of the functional fly: same feature inputs, same
hidden width budget, same per-step latency class. Readout weights can be
adapted online with a delta rule so imitation/RL signals are comparable.
"""

import numpy as np

from ..domain import NeuralState, NeuralStimulus
from .protocol import BrainBackend
from .random_reservoir import observation_features, _softmax

MODES = ("explore", "diagnose", "plan", "execute", "verify", "recover", "consolidate", "stop")
TOOLS = ("read_file", "list_files", "search", "git_status", "git_diff",
         "run_tests", "edit_file", "create_file", "docker_compose",
         "postgres_status", "http_request", "stop")


class GenericRNNBackend(BrainBackend):
    """Vanilla tanh RNN with trainable input/recurrent/readout weights."""

    name = "generic_rnn"
    tier = 0

    def __init__(
        self,
        hidden_size: int = 64,
        seed: int | None = None,
        learning_rate: float = 0.05,
    ) -> None:
        super().__init__(seed=seed)
        self.hidden_size = hidden_size
        self.learning_rate = learning_rate
        self.max_update_norm = 1.0  # online-learning limit (plan §57)
        self._rng = np.random.default_rng(seed if seed is not None else 0)

        scale = 1.0 / np.sqrt(hidden_size)
        self.w_in = self._rng.normal(0.0, scale, size=(hidden_size, 12))
        self.w_rec = self._rng.normal(0.0, scale, size=(hidden_size, hidden_size))
        self.w_out = self._rng.normal(0.0, scale, size=(len(MODES) + len(TOOLS), hidden_size))
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._steps = 0
        self._h = np.zeros(self.hidden_size, dtype=np.float64)
        self._state = NeuralState(state_vector=[0.0] * min(16, self.hidden_size), behavior_mode="explore")

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        x = observation_features(stimulus)
        self._h = np.tanh(self.w_in @ x + self.w_rec @ self._h)
        self._steps += 1

        out = np.tanh(self.w_out @ self._h)
        mode_scores = out[: len(MODES)]
        tool_scores = out[len(MODES):]

        mode_probs = _softmax(mode_scores * 2.0)
        mode = MODES[int(np.argmax(mode_probs))]
        action_bias = {
            tool: float(np.clip(score, -1.0, 1.0))
            for tool, score in zip(TOOLS, tool_scores)
        }

        self._last_input = x
        self._state = NeuralState(
            state_vector=self._h[:16].tolist(),
            mode_probs=dict(zip(MODES, mode_probs.tolist())),
            behavior_mode=mode,
            action_bias=action_bias,
            exploration_bias=float(np.clip(np.std(self._h), 0.0, 1.0)),
            valence=float(np.tanh(np.mean(self._h))),
            novelty=float(np.clip(np.std(self._h) * 2, 0.0, 1.0)),
            confidence=float(np.clip(1.0 - np.std(self._h), 0.0, 1.0)),
            is_learning=self._learning_enabled,
        )
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def close(self) -> None:
        self._h = np.zeros(0)

    # ------------------------------------------------------------ adaptation
    def adapt(self, target_bias: dict[str, float], lr: float | None = None) -> float:
        """One delta-rule update of the readout towards `target_bias`.

        Returns the L2 norm of the applied update (bounded by max_update_norm).
        """
        if not self._learning_enabled:
            return 0.0
        lr = self.learning_rate if lr is None else lr

        target = np.zeros(len(MODES) + len(TOOLS))
        for tool, value in target_bias.items():
            if tool in TOOLS:
                target[len(MODES) + TOOLS.index(tool)] = value

        current = np.tanh(self.w_out @ self._h)
        delta = (target - current) * (1.0 - current ** 2)
        update = lr * np.outer(delta, self._h)
        norm = float(np.linalg.norm(update))
        if norm > self.max_update_norm:
            update *= self.max_update_norm / norm
        self.w_out += update
        return float(np.linalg.norm(update))

    # ------------------------------------------------------------ matched compute
    def parameter_count(self) -> int:
        return int(self.w_in.size + self.w_rec.size + self.w_out.size)

    def get_computation_cost(self) -> float:
        return float(self.hidden_size * self.hidden_size)

    def get_memory_capacity(self) -> int:
        return self.hidden_size