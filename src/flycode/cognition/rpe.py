"""Reward Prediction Error (plan §15-17).

delta = r + gamma * V(s_next) - V(s)

A tabular/linear value estimate keyed on the observation signature is enough
for the functional fly: the point is not accuracy but making repeated,
already-predictable outcomes stop generating a large learning signal.
"""

import math

from pydantic import BaseModel


class PredictionError(BaseModel):
    predicted_value: float = 0.0
    observed_reward: float = 0.0
    next_value: float = 0.0
    delta: float = 0.0


class RPEModule:
    """Value-function-based prediction error with a bounded online update."""

    def __init__(self, gamma: float = 0.9, learning_rate: float = 0.1) -> None:
        if (not math.isfinite(gamma) or not 0 <= gamma <= 1
                or not math.isfinite(learning_rate) or learning_rate < 0):
            raise ValueError('Gamma and learning rate must be finite and in range')
        self.gamma = gamma
        self.learning_rate = min(learning_rate, .1)
        self.max_update = 0.5
        self.reset()

    def reset(self, *, clear_learning: bool = True) -> None:
        if clear_learning:
            self._values: dict[str, float] = {}
        self._last = PredictionError()

    def _value(self, signature: str) -> float:
        return self._values.get(signature, 0.0)

    def validate_limits(self) -> None:
        if (not all(math.isfinite(value) for value in (self.learning_rate, self.max_update, self.gamma))
                or self.learning_rate < 0 or self.max_update < 0 or not 0 <= self.gamma <= 1):
            raise ValueError('Learning limits must be finite and in range')

    def evaluate(
        self,
        previous_signature: str,
        reward: float,
        next_signature: str,
        *,
        learn: bool = True,
    ) -> PredictionError:
        self.validate_limits()
        if not math.isfinite(reward):
            raise ValueError('Reward must be finite')
        reward = max(-1., min(1., reward))
        predicted = self._value(previous_signature)
        next_value = self._value(next_signature)
        delta = reward + self.gamma * next_value - predicted

        if learn:
            limit = min(self.max_update, .5)
            update = max(-limit, min(limit, min(self.learning_rate, .1) * delta))
            self._values[previous_signature] = max(-10., min(10., predicted + update))
            if len(self._values) > 1024:
                self._values.pop(next(iter(self._values)))

        self._last = PredictionError(
            predicted_value=predicted,
            observed_reward=reward,
            next_value=next_value,
            delta=float(delta),
        )
        return self._last

    def get_state(self) -> PredictionError:
        return self._last

    def surprise(self) -> float:
        """Magnitude of the last prediction error, clipped to [0, 1]."""
        return float(max(0.0, min(1.0, abs(self._last.delta))))
