"""Task Compass - Central-Complex-like persistent task direction tracking (plan §10).

Keeps a low-dimensional direction vector that describes *where the solution is
heading* and how far the agent has drifted. When the agent repeats an action
family without verification, deviation grows and the compass raises the bias
for the corrective mode (plan §11).
"""

import numpy as np
from pydantic import BaseModel, Field

# Embedding of behavior modes into the compass space.
# Dimensions: [progress, information, verification, repair]
MODE_VECTORS: dict[str, tuple[float, float, float, float]] = {
    "explore": (0.30, 1.00, 0.10, 0.05),
    "diagnose": (0.20, 0.60, 0.30, 0.20),
    "plan": (0.55, 0.35, 0.20, 0.05),
    "execute": (0.95, 0.20, 0.10, 0.20),
    "verify": (0.45, 0.10, 1.00, 0.15),
    "recover": (0.35, 0.25, 0.45, 1.00),
    "consolidate": (0.30, 0.05, 0.35, 0.10),
    "stop": (1.00, 0.00, 0.60, 0.00),
}

COMPASS_DIM = 4


class TaskCompassState(BaseModel):
    """Persistent low-dimensional task state."""

    current_direction: list[float] = Field(default_factory=lambda: [0.0] * COMPASS_DIM)
    target_direction: list[float] = Field(default_factory=lambda: list(MODE_VECTORS["stop"]))

    deviation: float = Field(default=0.0, ge=0.0, le=1.0)
    persistence: float = Field(default=1.0, ge=0.0, le=1.0)
    strategy_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    mode: str = "explore"
    mode_dwell: int = 0
    reps_without_verify: int = Field(default=0, ge=0)

    def correction_pressure(self) -> float:
        """How strongly the compass wants a corrective mode switch (0..1)."""
        stagnation = min(1.0, self.reps_without_verify / 4.0)
        return float(np.clip(0.5 * self.deviation + 0.5 * stagnation, 0.0, 1.0))


class TaskCompass:
    """Controller that integrates behavior history into a persistent direction."""

    def __init__(self, dim: int = COMPASS_DIM, smoothing: float = 0.35, seed: int | None = None) -> None:
        self.dim = dim
        self.smoothing = smoothing
        self._rng = np.random.default_rng(seed if seed is not None else 0)
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self.current = np.zeros(self.dim, dtype=np.float64)
        self.mode = "explore"
        self.mode_dwell = 0
        self.reps_without_verify = 0
        self.history: list[str] = []
        self.state = TaskCompassState()

    # ------------------------------------------------------------ integration
    def target_for(self, mode: str) -> np.ndarray:
        vec = MODE_VECTORS.get(mode, MODE_VECTORS["execute"])
        arr = np.zeros(self.dim, dtype=np.float64)
        arr[: len(vec)] = vec[: self.dim]
        return arr

    def update(self, mode: str, mode_probs: dict[str, float] | None = None) -> TaskCompassState:
        """Integrate the newly selected mode into the compass direction."""
        target = self.target_for(mode)

        # Low-pass integration = persistent, inertial direction
        self.current = (1.0 - self.smoothing) * self.current + self.smoothing * target

        if mode == self.mode:
            self.mode_dwell += 1
        else:
            self.mode_dwell = 0
            self.mode = mode

        if mode in ("execute", "recover"):
            self.reps_without_verify += 1
        elif mode in ("verify", "consolidate"):
            self.reps_without_verify = 0

        self.history.append(mode)
        if len(self.history) > 32:
            self.history.pop(0)

        deviation = float(np.linalg.norm(target - self.current) / np.sqrt(self.dim))
        deviation = float(np.clip(deviation, 0.0, 1.0))

        # Persistence: how stable is the direction over the recent window
        if len(self.history) >= 4:
            recent = self.history[-8:]
            persistence = 1.0 - (len(set(recent)) - 1) / max(1, len(recent) - 1)
        else:
            persistence = 1.0

        if mode_probs:
            sorted_probs = sorted(mode_probs.values(), reverse=True)
            confidence = float(sorted_probs[0]) if sorted_probs else 0.5
        else:
            confidence = 1.0 - deviation

        self.state = TaskCompassState(
            current_direction=self.current.tolist(),
            target_direction=target.tolist(),
            deviation=deviation,
            persistence=float(np.clip(persistence, 0.0, 1.0)),
            strategy_confidence=float(np.clip(confidence, 0.0, 1.0)),
            mode=mode,
            mode_dwell=self.mode_dwell,
            reps_without_verify=self.reps_without_verify,
        )
        return self.state

    def correction_bias(self) -> dict[str, float]:
        """Mode bias produced by compass drift (plan §11)."""
        pressure = self.state.correction_pressure()
        if pressure <= 0.2:
            return {}
        bias = {"verify": pressure, "diagnose": 0.5 * pressure}
        if self.state.reps_without_verify >= 3:
            bias["execute"] = -pressure
        return bias