"""Reward Prediction Error (plan §15-17).

delta = r + gamma * V(s_next) - V(s)

A tabular/linear value estimate keyed on the observation signature is enough
for the functional fly: the point is not accuracy but making repeated,
already-predictable outcomes stop generating a large learning signal.
"""

from pydantic import BaseModel, Field


class PredictionError(BaseModel):
    predicted_value: float = 0.0
    observed_reward: float = 0.0
    next_value: float = 0.0
    delta: float = 0.0


class RPEModule:
    """Value-function-based prediction error with a bounded online update."""

    def __init__(self, gamma: float = 0.9, learning_rate: float = 0.1) -> None:
        self.gamma = gamma
        self.learning_rate = learning_rate
        self.max_update = 0.5
        self.reset()

    def reset(self) -> None:
        self._values: dict[str, float] = {}
        self._last = PredictionError()

    def _value(self, signature: str) -> float:
        return self._values.get(signature, 0.0)

    def evaluate(
        self,
        previous_signature: str,
        reward: float,
        next_signature: str,
        *,
        learn: bool = True,
    ) -> PredictionError:
        predicted = self._value(previous_signature)
        next_value = self._value(next_signature)
        delta = reward + self.gamma * next_value - predicted

        if learn:
            update = max(-self.max_update, min(self.max_update, self.learning_rate * delta))
            self._values[previous_signature] = predicted + update

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
