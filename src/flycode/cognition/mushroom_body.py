"""Mushroom Body - fast associative memory with sparse Kenyon-cell codes (plan §12-14).

Observation -> sparse projection -> sparse code -> action associations with
learned valence. Learning is a local Hebbian/delta rule so it can run online
inside the bounded-update budget (plan §56).
"""

import hashlib

import numpy as np
from pydantic import BaseModel, Field

from ..domain import Observation


class SparseCode(BaseModel):
    """Sparse activation pattern (2-10% active units)."""

    active_indices: list[int] = Field(default_factory=list)
    activity: list[float] = Field(default_factory=list)
    n_units: int = 0

    @property
    def sparsity(self) -> float:
        if self.n_units <= 0:
            return 1.0
        return len(self.active_indices) / self.n_units

    def key(self) -> str:
        return ",".join(str(i) for i in sorted(self.active_indices))


class MushroomBodyState(BaseModel):
    """Readout of the mushroom body module."""

    active_odes: list[int] = Field(default_factory=list)
    ode_activations: list[float] = Field(default_factory=list)
    memory_size: int = 0
    mean_credit: float = 0.0
    last_error: float = 0.0


class MushroomBody:
    """Associative memory: sparse observations -> action valence."""

    def __init__(
        self,
        n_features: int = 24,
        n_kenyon: int = 256,
        sparsity: float = 0.05,
        learning_rate: float = 0.05,
        seed: int | None = None,
    ) -> None:
        self.n_features = n_features
        self.n_kenyon = n_kenyon
        self.sparsity = float(np.clip(sparsity, 0.02, 0.10))
        if not np.isfinite(learning_rate) or learning_rate < 0:
            raise ValueError('Learning rate must be finite and nonnegative')
        self.learning_rate = min(learning_rate, 0.1)
        self.max_update_norm = 0.5
        self._rng = np.random.default_rng(seed if seed is not None else 0)
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self, *, clear_learning: bool = True) -> None:
        if not hasattr(self, 'projection'):
            self.projection = self._rng.normal(0.0, 1.0, size=(self.n_kenyon, self.n_features))
            self.projection *= self._rng.random((self.n_kenyon, self.n_features)) < 0.2
        if clear_learning:
            self.credit: dict[str, np.ndarray] = {}
            self.pattern_bank: dict[str, str] = {}
            self.updates = 0
        self.state = MushroomBodyState()

    # ------------------------------------------------------------ encoding
    @staticmethod
    def features(observation: Observation) -> np.ndarray:
        """Stable feature vector for the current observation."""
        obs = observation
        errs = " ".join(obs.recent_errors).lower()
        logs = obs.recent_logs
        return np.array(
            [
                1.0 if obs.tests_status == "passed" else 0.0,
                1.0 if obs.tests_status == "failed" else 0.0,
                1.0 if obs.build_status == "failed" else 0.0,
                1.0 if obs.build_status == "success" else 0.0,
                1.0 if obs.docker.get("running") else 0.0,
                1.0 if obs.docker.get("configured") else 0.0,
                1.0 if obs.database.get("connected") else 0.0,
                1.0 if obs.database.get("migrations_applied") else 0.0,
                1.0 if obs.services.get("app_running") else 0.0,
                1.0 if obs.git_state.get("dirty") else 0.0,
                float(min(1.0, obs.failing_tests / 5.0)),
                float(min(1.0, len(obs.modified_files) / 5.0)),
                float(min(1.0, len(obs.created_files) / 5.0)),
                float(min(1.0, obs.tool_calls_used / 30.0)),
                float(min(1.0, obs.step_number / 25.0)),
                1.0 if "connection refused" in errs else 0.0,
                1.0 if "modulenotfound" in errs else 0.0,
                1.0 if "syntaxerror" in errs else 0.0,
                1.0 if "assertionerror" in errs else 0.0,
                1.0 if "port" in errs else 0.0,
                float(min(1.0, len(set(logs)) / 10.0)),
                float(min(1.0, len(obs.recent_errors) / 5.0)),
                1.0 if obs.git_state.get("conflicts") else 0.0,
                float(min(1.0, len(obs.file_tree) / 40.0)),
            ],
            dtype=np.float64,
        )

    def encode(self, observation: Observation) -> SparseCode:
        """Project features to a sparse Kenyon-cell pattern."""
        x = self.features(observation)
        pre = self.projection @ x
        pre = pre - np.max(pre)

        k = max(1, int(round(self.n_kenyon * self.sparsity)))
        top = np.argpartition(pre, -k)[-k:]
        order = top[np.argsort(-pre[top])]
        activity = 1.0 / (1.0 + np.exp(-pre[order]))

        code = SparseCode(
            active_indices=[int(i) for i in order],
            activity=[float(a) for a in activity],
            n_units=self.n_kenyon,
        )
        self.pattern_bank[code.key()] = obs_digest(observation)
        if len(self.pattern_bank) > 1024:
            self.pattern_bank.pop(next(iter(self.pattern_bank)))
        return code

    # ------------------------------------------------------------ memory
    def _credit_for(self, action: str) -> np.ndarray:
        if action not in self.credit:
            if len(self.credit) >= 64:
                self.credit.pop(next(iter(self.credit)))
            self.credit[action] = np.zeros(self.n_kenyon, dtype=np.float64)
        return self.credit[action]

    def value(self, code: SparseCode, action: str) -> float:
        """Predicted valence of `action` under this sparse pattern."""
        credit = self.credit.get(action)
        if credit is None or not code.active_indices:
            return 0.0
        idx = np.asarray(code.active_indices, dtype=int)
        act = np.asarray(code.activity, dtype=np.float64)
        return float(np.sum(credit[idx] * act))

    def plan(self, code: SparseCode, actions: list[str]) -> dict[str, float]:
        return {action: self.value(code, action) for action in actions}

    def validate_limits(self) -> None:
        if (not np.isfinite([self.learning_rate, self.max_update_norm]).all()
                or self.learning_rate < 0 or self.max_update_norm < 0):
            raise ValueError('Learning limits must be finite and nonnegative')

    def learn(self, code: SparseCode, action: str, reward: float) -> float:
        """Hebbian update of the action credit under the active Kenyon cells."""
        self.validate_limits()
        if not np.isfinite(reward):
            raise ValueError('Learning reward must be finite')
        if not code.active_indices:
            return 0.0
        credit = self._credit_for(action)
        idx = np.asarray(code.active_indices, dtype=int)
        act = np.asarray(code.activity, dtype=np.float64)

        error = float(np.clip(reward, -1., 1.)) - self.value(code, action)
        raw_update = min(self.learning_rate, .1) * error * act
        norm = float(np.linalg.norm(raw_update))
        limit = min(self.max_update_norm, .5)
        if norm > limit:
            raw_update *= limit / norm

        before = credit[idx].copy()
        credit[idx] += raw_update
        np.clip(credit, -2.0, 2.0, out=credit)

        self.updates += 1
        self.state = MushroomBodyState(
            active_odes=list(code.active_indices),
            ode_activations=list(code.activity),
            memory_size=len(self.pattern_bank),
            mean_credit=float(np.mean(np.abs(credit))),
            last_error=float(error),
        )
        return float(np.linalg.norm(credit[idx] - before))

    def recall_best(self, code: SparseCode, actions: list[str]) -> tuple[str | None, float]:
        """Best remembered action for this pattern."""
        if not actions:
            return None, 0.0
        values = self.plan(code, actions)
        best = max(values, key=values.get)
        return best, values[best]

    def get_memory_capacity(self) -> int:
        return len(self.pattern_bank)


def obs_digest(observation: Observation) -> str:
    """Short digest of the observation for memory bookkeeping."""
    blob = f"{observation.token_signature()}|{observation.step_number}"
    return hashlib.sha1(blob.encode()).hexdigest()[:10]
