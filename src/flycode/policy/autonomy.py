"""Guard hard selection with actual-outcome fallback to bias control (§88)."""

from pydantic import BaseModel

from ..domain import CandidateAction, Transition
from .candidate_tournament import ControlMode, TournamentResult, run_tournament


class AutonomyDecision(BaseModel):
    requested_control: ControlMode = ControlMode.HARD_CONTROL
    applied_control: ControlMode
    hard_choice: str | None = None
    bias_choice: str | None = None
    executed_choice: str | None = None
    rolled_back: bool = False
    reason: str = 'validated hard selection'


class AutonomyController:
    def __init__(self) -> None:
        self.control_mode = ControlMode.HARD_CONTROL
        self.rollbacks = 0
        self.last: AutonomyDecision | None = None

    def select(self, candidates: list[CandidateAction], scores: dict[str, float]) -> TournamentResult:
        hard = run_tournament(candidates, scores, ControlMode.HARD_CONTROL)
        bias = run_tournament(candidates, scores, ControlMode.BIAS_CONTROL)
        selected = hard if self.control_mode is ControlMode.HARD_CONTROL else bias
        self.last = AutonomyDecision(applied_control=self.control_mode,
            hard_choice=hard.winner.id if hard.winner else None,
            bias_choice=bias.winner.id if bias.winner else None,
            executed_choice=selected.winner.id if selected.winner else None,
            reason='validated hard selection' if self.control_mode is ControlMode.HARD_CONTROL else 'bias fallback')
        return selected

    def observe(self, transition: Transition) -> None:
        if self.last is None or self.control_mode is not ControlMode.HARD_CONTROL:
            return
        changed = self.last.hard_choice != self.last.bias_choice and transition.action.id == self.last.executed_choice
        if transition.is_regression or (changed and not transition.success):
            self.control_mode = ControlMode.BIAS_CONTROL
            self.rollbacks += 1
            self.last.rolled_back = True
            self.last.reason = 'regression' if transition.is_regression else 'hard choice failed; using bias fallback'
