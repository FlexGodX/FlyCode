"""Bounded diagnostic intervention after active selection; safety remains final (§60,87)."""

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain import CandidateAction, Transition
from ..safety import assess_risk
from .candidate_tournament import ControlMode, TournamentResult, run_tournament

DIAGNOSTIC_TOOLS = frozenset({'read_file', 'list_files', 'search', 'git_status', 'git_diff',
                            'docker_logs', 'postgres_status', 'service_health'})


class RolloutConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

    influence: float = Field(default=0., ge=0, le=1)

    @field_validator('influence')
    @classmethod
    def supported_level(cls, value: float) -> float:
        if value not in {0., .1, .25, .5, 1.}:
            raise ValueError('Influence must be one of 0, .1, .25, .5, 1')
        return value


class RolloutDecision(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    requested_influence: float = Field(ge=0, le=1)
    applied_influence: float = Field(default=0., ge=0, le=1)
    active_choice: str | None = None
    observer_choice: str | None = None
    executed_choice: str | None = None
    corrections: dict[str, float] = Field(default_factory=dict)
    intervened: bool = False
    rolled_back: bool = False
    reason: str


class RolloutState(BaseModel):
    disabled: bool = False
    interventions: int = 0
    rollbacks: int = 0
    last_reason: str = 'no intervention'


def _diagnostic(candidate: CandidateAction) -> bool:
    risk = assess_risk(candidate)
    return candidate.tool in DIAGNOSTIC_TOOLS and not risk.is_blocked and risk.risk_level < .4


class RolloutController:
    def __init__(self, influence: float = 0.) -> None:
        self.config = RolloutConfig(influence=influence)
        self.reset()

    def reset(self) -> None:
        self.state = RolloutState()
        self.last: RolloutDecision | None = None

    def select(self, candidates: list[CandidateAction], active: TournamentResult,
               observer_scores: dict[str, float], *, observer_error: str | None = None) -> TournamentResult:
        choice = active.winner.id if active.winner is not None else None
        decision = RolloutDecision(requested_influence=self.config.influence, active_choice=choice,
                                   executed_choice=choice, reason='shadow only')
        self.last = decision

        def unchanged(reason: str) -> TournamentResult:
            decision.reason = reason
            self.state.last_reason = reason
            return active

        if self.config.influence == 0.:
            return unchanged('shadow only')
        if self.state.disabled:
            return unchanged('influence disabled after outcome')
        if observer_error:
            return unchanged('observer unavailable: ' + observer_error)
        if active.mode is not ControlMode.BIAS_CONTROL:
            return unchanged('bounded influence requires bias control')
        if len({candidate.id for candidate in candidates}) != len(candidates):
            return unchanged('duplicate candidate IDs')
        try:
            if not all(math.isfinite(value) for value in [*active.scores.values(), *observer_scores.values()]):
                return unchanged('nonfinite candidate scores')
        except (TypeError, ValueError, OverflowError):
            return unchanged('invalid candidate scores')
        if active.winner is None or not _diagnostic(active.winner):
            return unchanged('active action outside diagnostic scope')
        pool = [candidate for candidate in candidates if candidate.id in active.scores and _diagnostic(candidate)]
        if len(pool) < 2 or active.winner.id not in {candidate.id for candidate in pool}:
            return unchanged('no admissible diagnostic alternatives')
        proposal = run_tournament(candidates, observer_scores, active.mode).winner
        corrections = {candidate.id: .2 * self.config.influence * max(-1., min(1., observer_scores.get(candidate.id, 0.)))
                       for candidate in pool}
        scores = {candidate.id: active.scores[candidate.id] + corrections[candidate.id] for candidate in pool}
        winner = max(pool, key=lambda candidate: scores[candidate.id])
        self.last.observer_choice = proposal.id if proposal is not None else None
        self.last.executed_choice = winner.id
        self.last.applied_influence = self.config.influence
        self.last.corrections = corrections
        self.last.intervened = winner.id != active.winner.id
        self.last.reason = 'bounded diagnostic ranking'
        self.state.last_reason = self.last.reason
        return active.model_copy(update={'winner': winner, 'scores': scores})

    def observe(self, transition: Transition) -> None:
        intervened = bool(self.last and self.last.intervened and transition.action.id == self.last.executed_choice)
        self.state.interventions += int(intervened)
        if (self.config.influence and not self.state.disabled
                and (transition.is_regression or (intervened and not transition.success))):
            self.state.disabled = True
            self.state.rollbacks += 1
            self.state.last_reason = 'regression' if transition.is_regression else 'intervened action failed'
            if self.last is not None:
                self.last.rolled_back = True
                self.last.reason = self.state.last_reason
