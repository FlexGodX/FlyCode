"""Mock brain backend - deterministic, dependency-free control for tests."""

import numpy as np

from ..domain import NeuralState, NeuralStimulus
from .protocol import BrainBackend


class MockBrainBackend(BrainBackend):
    """Minimal deterministic controller.

    Extracts a handful of features from the observation and returns a
    reproducible state. Used as a smoke-test backend and as the reference
    implementation for the `BrainBackend` contract.
    """

    name = "mock"
    tier = 0

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self._rng = np.random.default_rng(seed if seed is not None else 0)
        self._history: list[str] = []
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._steps = 0
        self._history.clear()
        self._state = NeuralState(
            state_vector=[0.0] * 8,
            behavior_mode="explore",
            confidence=0.5,
        )

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        obs = stimulus.observation
        self._history.append(obs.token_signature())
        self._steps += 1

        errors = len(obs.recent_errors)
        failures = obs.failing_tests
        build_failed = obs.build_status == "failed"

        features = [
            float(obs.step_number),
            float(len(obs.recent_logs)),
            float(errors),
            float(failures),
            float(build_failed),
            float(len(obs.modified_files)),
            float(obs.git_state.get("dirty", 0) if isinstance(obs.git_state.get("dirty"), int) else int(bool(obs.git_state.get("dirty")))),
            float(obs.tool_calls_used),
        ]

        risk = 1.0 if stimulus.target_behavior.lower() in {"delete", "rm", "drop", "reset"} else 0.0

        if build_failed or failures > 0:
            mode = "diagnose"
        elif errors == 0 and obs.tests_status == "passed":
            mode = "consolidate"
        else:
            mode = "execute"

        self._state = NeuralState(
            state_vector=features,
            behavior_mode=mode,
            exploration_bias=min(1.0, errors * 0.1),
            valence=-min(1.0, (errors + failures) * 0.1),
            novelty=min(1.0, len(set(obs.recent_logs)) * 0.05),
            confidence=max(0.3, 1.0 - (errors + failures) * 0.1),
            risk_level=risk,
            action_bias={"read_file": 0.6, "run_tests": 0.4, "edit_file": 0.2, "stop": 0.1},
        )
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def close(self) -> None:
        self._history.clear()