"""Random reservoir (echo-state network) baseline controller."""

import numpy as np

from ..domain import NeuralState, NeuralStimulus
from .protocol import BrainBackend

N_FEATURES = 12


def observation_features(stimulus: NeuralStimulus) -> np.ndarray:
    """Map an observation into a fixed-length numeric feature vector."""
    obs = stimulus.observation
    tests = obs.tests
    features = np.zeros(N_FEATURES, dtype=np.float64)

    features[0] = min(1.0, obs.step_number / 25.0)
    features[1] = min(1.0, len(obs.recent_logs) / 20.0)
    features[2] = min(1.0, len(obs.recent_errors) / 10.0)
    features[3] = min(1.0, int(tests.get("passed", 0) or 0) / 20.0)
    features[4] = min(1.0, obs.failing_tests / 10.0)
    features[5] = 1.0 if obs.tests_status == "passed" else 0.0
    features[6] = 1.0 if obs.build_status == "failed" else 0.0
    features[7] = 1.0 if obs.build_status == "success" else 0.0
    features[8] = min(1.0, len(obs.modified_files) / 10.0)
    features[9] = min(1.0, obs.tool_calls_used / 40.0)
    features[10] = 1.0 if obs.docker.get("running") else 0.0
    features[11] = 1.0 if obs.database.get("connected") else 0.0
    return features


class RandomReservoirBackend(BrainBackend):
    """Leaky echo-state reservoir with a fixed random readout.

    This is a *baseline*: the recurrent weights are never trained, only the
    linear readout is adapted (optionally) with a delta rule. Latency and
    parameter count are reported for matched-compute comparison.
    """

    name = "random_reservoir"
    tier = 0

    MODES = ("explore", "diagnose", "plan", "execute", "verify", "recover", "consolidate", "stop")
    TOOLS = ("read_file", "list_files", "search", "git_status", "git_diff",
             "run_tests", "edit_file", "create_file", "docker_compose",
             "postgres_status", "http_request", "stop")

    def __init__(
        self,
        n_neurons: int = 256,
        spectral_radius: float = 0.9,
        leak: float = 0.3,
        seed: int | None = None,
    ) -> None:
        super().__init__(seed=seed)
        self.n_neurons = n_neurons
        self.spectral_radius = spectral_radius
        self.leak = leak
        self._rng = np.random.default_rng(seed if seed is not None else 0)

        self.w_in = self._rng.normal(0.0, 1.0, size=(n_neurons, N_FEATURES)) / np.sqrt(N_FEATURES)
        w = self._rng.normal(0.0, 1.0, size=(n_neurons, n_neurons))
        w *= (self._rng.random((n_neurons, n_neurons)) < 0.1)
        radius = max(1e-6, float(np.max(np.abs(np.linalg.eigvals(w)))))
        self.w_rec = (w * (spectral_radius / radius)).astype(np.float64)
        self.w_bias = self._rng.normal(0.0, 0.1, size=(len(self.MODES) + len(self.TOOLS), n_neurons))
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._steps = 0
        self._h = np.zeros(self.n_neurons, dtype=np.float64)
        self._state = NeuralState(state_vector=[0.0] * min(16, self.n_neurons), behavior_mode="explore")

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        x = observation_features(stimulus)
        pre = self.w_rec @ self._h + self.w_in @ x
        self._h = (1.0 - self.leak) * self._h + self.leak * np.tanh(pre)
        self._steps += 1

        out = np.tanh(self.w_bias @ self._h)
        mode_scores = out[: len(self.MODES)]
        tool_scores = out[len(self.MODES):]

        mode_probs = _softmax(mode_scores * 2.0)
        mode = self.MODES[int(np.argmax(mode_probs))]

        action_bias = {
            tool: float(np.clip(score, -1.0, 1.0))
            for tool, score in zip(self.TOOLS, tool_scores)
        }

        self._state = NeuralState(
            state_vector=self._h[:16].tolist(),
            mode_probs=dict(zip(self.MODES, mode_probs.tolist())),
            behavior_mode=mode,
            action_bias=action_bias,
            exploration_bias=float(0.5 * (1.0 + np.mean(np.abs(self._h)))),
            valence=float(np.tanh(np.mean(self._h))),
            novelty=float(np.std(self._h)),
            confidence=float(np.clip(1.0 - np.std(self._h), 0.0, 1.0)),
            risk_level=float(np.clip(abs(action_bias.get("docker_compose", 0.0)), 0.0, 1.0)),
        )
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def close(self) -> None:
        self._h = np.zeros(0)

    # ------------------------------------------------------------ matched compute
    def parameter_count(self) -> int:
        return int(self.w_rec.size + self.w_in.size + self.w_bias.size)

    def get_computation_cost(self) -> float:
        return float(self.n_neurons * self.n_neurons)

    def get_memory_capacity(self) -> int:
        return self.n_neurons


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values)
    exp = np.exp(shifted)
    total = np.sum(exp)
    if total <= 0:
        return np.full_like(values, 1.0 / len(values))
    return exp / total