"""Bounded replay and detached policy snapshots for online learning (§57)."""

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .mushroom_body import MushroomBody, SparseCode


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    encoding_sha256: str
    credits: dict[str, list[float]] = Field(default_factory=dict)
    rpe_values: dict[str, float] = Field(default_factory=dict)
    updates: int = Field(default=0, ge=0)


class ReplaySample(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    code: SparseCode
    action: str
    reward: float = Field(ge=-1., le=1.)


class LearningRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    format_version: Literal[1] = 1
    policy: PolicySnapshot
    replay: list[ReplaySample] = Field(default_factory=list, max_length=128)


class AdaptationState(BaseModel):
    accepted_updates: int = 0
    rollbacks: int = 0
    max_applied_norm: float = 0.
    last_applied_norm: float = 0.
    last_proposed_norm: float = 0.
    last_policy_change: float = 0.
    last_replay_error: float = 0.
    last_reason: str = 'no update'


class AdaptationGuard:
    def __init__(self) -> None:
        self.max_policy_change = .25
        self.max_replay_regression = .001
        self.clear()

    def clear(self) -> None:
        self.replay: list[ReplaySample] = []
        self.state = AdaptationState()

    def remember(self, code: SparseCode, action: str, reward: float) -> None:
        sample = ReplaySample(code=code.model_copy(deep=True), action=action, reward=reward)
        # New evidence for the same situation replaces stale labels after reversal.
        self.replay = [old for old in self.replay if (old.code.key(), old.action) != (code.key(), action)]
        self.replay.append(sample)
        self.replay = self.replay[-128:]

    def predictions(self, memory: MushroomBody) -> np.ndarray:
        return np.array([memory.value(sample.code, sample.action) for sample in self.replay])

    def validate_limits(self) -> None:
        if (not np.isfinite([self.max_policy_change, self.max_replay_regression]).all()
                or not 0 <= self.max_policy_change <= .25 or not 0 <= self.max_replay_regression <= .001):
            raise ValueError('Adaptation limits must be finite and within policy bounds')

    def accept(self, memory: MushroomBody, before: np.ndarray, norm: float) -> bool:
        after = self.predictions(memory)
        targets = np.array([sample.reward for sample in self.replay])
        drift = float(np.max(np.abs(after - before)))
        previous_error = float(np.mean((before - targets) ** 2))
        error = float(np.mean((after - targets) ** 2))
        reason = ('nonfinite update' if not np.isfinite(after).all() else
                  'policy drift' if drift > self.max_policy_change else
                  'replay regression' if error > previous_error + self.max_replay_regression else 'accepted')
        self.state.last_proposed_norm = norm
        self.state.last_applied_norm = norm if reason == 'accepted' else 0.
        self.state.max_applied_norm = max(self.state.last_applied_norm, self.state.max_applied_norm)
        self.state.last_policy_change = drift
        self.state.last_replay_error = error
        self.state.last_reason = reason
        if reason == 'accepted':
            self.state.accepted_updates += 1
            return True
        self.state.rollbacks += 1
        return False
