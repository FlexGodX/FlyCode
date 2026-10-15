"""Zero-influence observer inside FlyCode, preserving active routing (§58–59,86)."""

import time

from ..domain import (
    CandidateAction,
    NeuralState,
    NeuralStimulus,
    ToolAction,
    Transition,
)
from ..policy import ControlMode, run_tournament
from .protocol import BrainBackend


def _scores(brain: BrainBackend, candidates: list[CandidateAction], mode: str) -> dict[str, float]:
    if hasattr(brain, 'score_candidates'):
        return brain.score_candidates(candidates, mode)
    return {candidate.id: brain.get_policy_bias().get(candidate.tool, 0.) for candidate in candidates}


class ShadowBackend(BrainBackend):
    def __init__(self, active: BrainBackend, observer: BrainBackend) -> None:
        super().__init__(seed=active.seed)
        self.active, self.observer = active, observer
        self.name = f'shadow({active.name},{observer.name})'
        self.tier = active.tier
        self._learning_enabled = active.learning_enabled()
        self._observer_error: str | None = None
        observer.set_learning_mode(False)
        for attribute in ('adaptation', 'rpe', 'habituation'):
            if hasattr(active, attribute):
                setattr(self, attribute, getattr(active, attribute))
        self.reset()

    def _observe(self, method: str, *args):
        if self._observer_error is not None or not hasattr(self.observer, method):
            return None
        try:
            return getattr(self.observer, method)(*args)
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            self._observer_error = str(exc)
            return None

    def reset(self) -> None:
        self.active.reset()
        self._state = self.active.get_state()
        self._steps = 0
        self._observer_error = None
        self._observer_latency = 0.
        self._observe('reset')

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        self._state = self.active.step(stimulus)
        self._steps += 1
        start = time.perf_counter()
        self._observe('step', stimulus)
        self._observer_latency = (time.perf_counter() - start) * 1000.
        return self._state

    def get_state(self) -> NeuralState:
        return self.active.get_state()

    def score_candidates(self, candidates: list[CandidateAction], mode: str) -> dict[str, float]:
        return _scores(self.active, candidates, mode)

    def shadow_decision(self, candidates: list[CandidateAction], mode: ControlMode, actual: CandidateAction) -> dict:
        start = time.perf_counter()
        recommendation = None
        if self._observer_error is None:
            try:
                state = self.observer.get_state()
                recommendation = run_tournament(candidates, _scores(self.observer, candidates, state.behavior_mode), mode).winner
            except (ValueError, RuntimeError, ArithmeticError) as exc:
                self._observer_error = str(exc)
        return {'influence': 0., 'active_choice': actual.id,
                'shadow_choice': recommendation.id if recommendation else None,
                'agreement': recommendation.id == actual.id if recommendation else None,
                'risk_delta': recommendation.expected_risk - actual.expected_risk if recommendation else None,
                'observer_latency_ms': self._observer_latency + (time.perf_counter() - start) * 1000.,
                'counterfactual_success': None, 'tool_call_delta': None, 'error': self._observer_error}

    def record_outcome(self, action: ToolAction, mode: str, utility: float, success: bool) -> None:
        if hasattr(self.active, 'record_outcome'):
            self.active.record_outcome(action, mode, utility, success)
        self._observe('record_outcome', action, mode, utility, success)

    def learn_transition(self, transition: Transition) -> None:
        if hasattr(self.active, 'learn_transition'):
            self.active.learn_transition(transition)
        self._observe('learn_transition', transition)

    def set_learning_mode(self, enabled: bool) -> None:
        self.active.set_learning_mode(enabled)
        super().set_learning_mode(enabled)
        self.observer.set_learning_mode(False)

    def save_learning(self, directory) -> None:
        if not hasattr(self.active, 'save_learning'):
            raise ValueError('Active controller does not support learning persistence')
        self.active.save_learning(directory)

    def parameter_count(self) -> int:
        return self.active.parameter_count() + self.observer.parameter_count()

    def get_computation_cost(self) -> float:
        return self.active.get_computation_cost() + self.observer.get_computation_cost()

    def describe(self) -> dict:
        return {**super().describe(), 'active': self.active.describe(), 'observer': self.observer.describe(), 'influence': 0.}

    def close(self) -> None:
        self.active.close()
        try:
            self.observer.close()
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            self._observer_error = str(exc)
