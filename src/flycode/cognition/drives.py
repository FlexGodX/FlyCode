"""Internal drives (plan §20).

A small set of 0..1 scalars summarizing the agent's internal pressure state.
These feed mode arbitration (e.g. high stagnation + low novelty -> raise
EXPLORE bias, plan §22) without being a full behavior-mode decision on their
own.
"""

from pydantic import BaseModel, Field

from ..domain import Observation


class DriveState(BaseModel):
    progress: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty_need: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.5, ge=0.0, le=1.0)
    stress: float = Field(default=0.0, ge=0.0, le=1.0)
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    stagnation: float = Field(default=0.0, ge=0.0, le=1.0)
    repetition: float = Field(default=0.0, ge=0.0, le=1.0)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    context_pressure: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def wants_exploration(self) -> bool:
        return self.progress < 0.3 and self.novelty_need > 0.6

    @property
    def wants_recovery(self) -> bool:
        return self.stress > 0.6 or self.risk > 0.6


class DriveModule:
    """Derives the drive vector from observation history and repeated actions."""

    def __init__(self, window: int = 8) -> None:
        self.window = window
        self.reset()

    def reset(self) -> None:
        self._recent_signatures: list[str] = []
        self._recent_fingerprints: list[str] = []
        self._steps_since_progress = 0
        self._best_failing = None
        self.state = DriveState()

    def update(
        self,
        observation: Observation,
        *,
        action_fingerprint: str | None = None,
        novelty: float = 0.5,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
    ) -> DriveState:
        self._recent_signatures.append(observation.token_signature())
        if len(self._recent_signatures) > self.window:
            self._recent_signatures.pop(0)

        if action_fingerprint is not None:
            self._recent_fingerprints.append(action_fingerprint)
            if len(self._recent_fingerprints) > self.window:
                self._recent_fingerprints.pop(0)

        failing = observation.failing_tests
        if self._best_failing is None or failing < self._best_failing:
            self._best_failing = failing
            self._steps_since_progress = 0
        else:
            self._steps_since_progress += 1

        progress = 1.0 if failing == 0 and observation.tests_status == "passed" else max(
            0.0, 1.0 - min(1.0, failing / 10.0)
        )
        stagnation = min(1.0, self._steps_since_progress / max(1, self.window))

        repetition = 0.0
        if len(self._recent_fingerprints) >= 2:
            unique = len(set(self._recent_fingerprints))
            repetition = 1.0 - unique / len(self._recent_fingerprints)

        distinct_obs = len(set(self._recent_signatures))
        uncertainty = 1.0 - distinct_obs / max(1, len(self._recent_signatures))

        stress = min(1.0, 0.5 * bool(observation.recent_errors) + 0.5 * (observation.build_status == "failed"))

        urgency = 0.0
        if max_steps:
            urgency = max(urgency, min(1.0, observation.step_number / max_steps))
        if max_tool_calls:
            urgency = max(urgency, min(1.0, observation.tool_calls_used / max_tool_calls))

        risk = 1.0 if observation.git_state.get("conflicts") else 0.0

        self.state = DriveState(
            progress=float(progress),
            novelty_need=float(max(0.0, min(1.0, stagnation * (1.0 - novelty)))),
            uncertainty=float(max(0.0, min(1.0, uncertainty))),
            stress=float(stress),
            urgency=float(urgency),
            stagnation=float(stagnation),
            repetition=float(max(0.0, min(1.0, repetition))),
            risk=float(risk),
            context_pressure=float(max(urgency, stress)),
        )
        return self.state

    def get_state(self) -> DriveState:
        return self.state
