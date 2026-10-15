"""Functional Fly - production-oriented controller (plan §41).

Composes the five cognition modules extracted from fly research into one
BrainBackend: Task Compass (persistent direction), Mushroom Body (fast
associative memory), Valence (dual utility/aversion), Novelty (curiosity),
RPE (reward prediction error) and a lightweight habituation tracker. This is
Tier 1 - it never touches a real connectome; it is the thing distillation
(plan §42-43) eventually targets.
"""

from pathlib import Path

import numpy as np

from ..cognition import (
    DriveModule,
    HabituationModule,
    MushroomBody,
    NoveltyModule,
    RPEModule,
    TaskCompass,
    ValenceModule,
)
from ..domain import (
    VALID_TOOLS,
    VALID_TRANSITIONS,
    BehaviorMode,
    CandidateAction,
    NeuralState,
    NeuralStimulus,
    ToolAction,
)
from .protocol import BrainBackend

MODE_TOOL_TABLE: dict[str, dict[str, float]] = {
    "explore": {"read_file": 0.9, "list_files": 0.7, "search": 0.5, "git_status": 0.4},
    "diagnose": {"run_tests": 0.9, "git_diff": 0.6, "read_file": 0.5, "docker_logs": 0.3},
    "plan": {"read_file": 0.5, "search": 0.4, "list_files": 0.3},
    "execute": {"edit_file": 0.9, "create_file": 0.5, "docker_compose": 0.4},
    "verify": {"run_tests": 0.9, "http_request": 0.5, "service_health": 0.5, "git_diff": 0.3},
    "recover": {"run_tests": 0.8, "git_diff": 0.6, "edit_file": 0.6, "docker_compose": 0.5},
    "consolidate": {"stop": 0.6, "git_diff": 0.3},
    "stop": {"stop": 1.0},
}

ALL_MODES: tuple[str, ...] = tuple(m.value for m in BehaviorMode)


class FunctionalFlyBackend(BrainBackend):
    """Tier-1 controller: Task Compass + Mushroom Body + Valence + Novelty + RPE."""

    name = "functional_fly"
    tier = 1

    def __init__(
        self,
        n_kenyon: int = 256,
        sparsity: float = 0.05,
        seed: int | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> None:
        super().__init__(seed=seed)
        self.compass = TaskCompass(seed=seed)
        self.mushroom_body = MushroomBody(n_kenyon=n_kenyon, sparsity=sparsity, seed=seed)
        self.valence = ValenceModule()
        self.novelty = NoveltyModule()
        self.rpe = RPEModule()
        self.drives = DriveModule()
        self.habituation = HabituationModule()
        self._distilled_readout = None
        if checkpoint_path is not None:
            from ..training.student import load_student

            self._distilled_readout = load_student(Path(checkpoint_path))
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._steps = 0
        self.compass.reset()
        self.mushroom_body.reset()
        self.valence.reset()
        self.novelty.reset()
        self.rpe.reset()
        self.drives.reset()
        self.habituation.reset()
        self._last_observation = None
        self._last_code = None
        self._last_tool: str | None = None
        self._executed_tool: str | None = None
        self._state = NeuralState(behavior_mode="explore")

    # ------------------------------------------------------------ step
    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        obs = stimulus.observation
        self._steps += 1

        novelty_state = self.novelty.evaluate(obs)
        code = self.mushroom_body.encode(obs)

        # Learn from the transition implied by (last_observation -> obs), if any.
        reward = 0.0
        if self._last_observation is not None:
            # Prefer the actually-executed tool (set via record_outcome) over
            # our own prior guess, so internal credit assignment tracks what
            # really happened rather than what the fly biased toward.
            tool = self._executed_tool or self._last_tool or stimulus.target_behavior
            synthetic_action = ToolAction(
                id="internal",
                tool=tool,
                expected_risk=min(1.0, max(0.0, 1.0 - stimulus.priority)) * 0.2,
            )
            signals = self.valence.evaluate(synthetic_action, self._last_observation, obs)
            reward = signals.utility - 0.7 * signals.aversion
            if self._last_code is not None and self._learning_enabled:
                self.mushroom_body.learn(self._last_code, synthetic_action.tool, reward)
            self.rpe.evaluate(
                self._last_observation.token_signature(),
                reward,
                obs.token_signature(),
                learn=self._learning_enabled,
            )

        drive_state = self.drives.update(
            obs,
            action_fingerprint=self._last_tool,
            novelty=novelty_state.novelty,
        )

        mode, mode_probs = self._select_mode(obs, drive_state, novelty_state.novelty)
        self.compass.update(mode, mode_probs)
        action_bias = self._action_bias(mode, code, obs)

        risk = 1.0 if stimulus.target_behavior.lower() in {"delete", "rm", "drop", "reset"} else 0.0

        self._state = NeuralState(
            state_vector=self._state_vector(obs, drive_state),
            task_direction=self.compass.state.current_direction,
            deviation=self.compass.state.deviation,
            persistence=self.compass.state.persistence,
            exploration_bias=drive_state.novelty_need,
            valence=float(np.clip(reward, -1.0, 1.0)),
            novelty=novelty_state.novelty,
            urgency=drive_state.urgency,
            confidence=self.compass.state.strategy_confidence,
            active_odes=code.active_indices,
            recent_actions=self.habituation.recent(8),
            risk_level=max(risk, drive_state.risk),
            is_learning=self._learning_enabled,
            update_weight=abs(self.rpe.get_state().delta) if self._last_observation is not None else 0.0,
            behavior_mode=mode,
            mode_probs=mode_probs,
            action_bias=action_bias,
        )

        self._last_observation = obs
        self._last_code = code
        self._last_tool = self._dominant_tool(action_bias)
        self._executed_tool = None  # consumed; record_outcome() sets it again if called
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def close(self) -> None:
        pass

    # ------------------------------------------------------------ mode arbitration
    def _select_mode(self, obs, drive_state, novelty: float) -> tuple[str, dict[str, float]]:
        failing = obs.failing_tests
        build_failed = obs.build_status == "failed"
        healthy = obs.is_healthy

        needs_fixing = failing > 0 or build_failed or bool(obs.recent_errors)
        # The longer diagnose/explore run without progress (rising stagnation),
        # the more they yield to execute - diagnosis that never acts is useless
        # repetition (plan §21), and stagnation is exactly the signal for that.
        scores = {
            "explore": 0.3 + 0.5 * drive_state.novelty_need,
            "diagnose": 0.6 * needs_fixing * (1.0 - 0.5 * drive_state.stagnation),
            "plan": 0.25,
            "execute": (0.3 + 0.6 * drive_state.stagnation) * float(needs_fixing),
            "verify": 0.4 + 0.5 * self.compass.state.correction_pressure(),
            "recover": 0.7 * float(drive_state.wants_recovery),
            "consolidate": 0.8 * float(healthy),
            "stop": 0.9 * float(healthy and self.compass.mode == "consolidate" and self.compass.mode_dwell >= 1),
        }

        for mode, pressure in self.compass.correction_bias().items():
            scores[mode] = scores.get(mode, 0.0) + pressure

        current = self.compass.mode
        for candidate in VALID_TRANSITIONS.get(BehaviorMode(current), ()):
            scores[candidate.value] = scores.get(candidate.value, 0.0) + 0.05
        scores[current] = scores.get(current, 0.0) + 0.05  # mild hysteresis

        mode = max(scores, key=scores.get)
        probs = _softmax_dict(scores)
        return mode, probs

    # ------------------------------------------------------------ action bias
    def _action_bias(self, mode: str, code, observation) -> dict[str, float]:
        table = dict(MODE_TOOL_TABLE.get(mode, {"read_file": 0.5}))
        if self._distilled_readout is not None:
            from ..training.student import student_features

            table = dict(zip(VALID_TOOLS, np.tanh(self._distilled_readout @ student_features(observation))))
        for tool in list(table.keys()):
            credit = self.mushroom_body.value(code, tool)
            discount = self.habituation.discount(ToolAction(id="probe", tool=tool), mode)
            table[tool] = float(np.clip(table[tool] + 0.3 * credit, -1.0, 1.0)) * discount
        return table

    @staticmethod
    def _dominant_tool(action_bias: dict[str, float]) -> str | None:
        if not action_bias:
            return None
        return max(action_bias, key=action_bias.get)

    @staticmethod
    def _state_vector(obs, drive_state) -> list[float]:
        return [
            float(obs.step_number),
            float(obs.failing_tests),
            float(len(obs.recent_errors)),
            float(obs.build_status == "failed"),
            drive_state.progress,
            drive_state.stagnation,
            drive_state.repetition,
            drive_state.uncertainty,
        ]

    # ------------------------------------------------------------ candidate scoring (plan §26)
    def score_candidates(self, candidates: list[CandidateAction], mode: str | None = None) -> dict[str, float]:
        """Per-candidate fly bias for the Candidate Tournament / bias control."""
        mode = mode or self._state.behavior_mode
        code = self._last_code
        scores: dict[str, float] = {}
        for candidate in candidates:
            base = self._state.action_bias.get(candidate.tool, 0.0)
            credit = self.mushroom_body.value(code, candidate.tool) if code is not None else 0.0
            discount = self.habituation.discount(candidate, mode)
            scores[candidate.id] = float(np.clip(base + 0.3 * credit, -1.0, 1.0)) * discount
        return scores

    def record_outcome(self, action: ToolAction, mode: str, utility: float, success: bool) -> None:
        """Feed an executed action's outcome back into habituation tracking."""
        self.habituation.record(action, mode, utility, success)
        self._executed_tool = action.tool

    # ------------------------------------------------------------ matched compute
    def parameter_count(self) -> int:
        return int(self.mushroom_body.projection.size) + sum(
            v.size for v in self.mushroom_body.credit.values()
        ) + (self._distilled_readout.size if self._distilled_readout is not None else 0)

    def get_memory_capacity(self) -> int:
        return self.mushroom_body.get_memory_capacity()

    def get_computation_cost(self) -> float:
        return float(self.mushroom_body.n_kenyon +
                     (self._distilled_readout.size if self._distilled_readout is not None else 0))


def _softmax_dict(scores: dict[str, float]) -> dict[str, float]:
    values = np.array(list(scores.values()), dtype=np.float64)
    shifted = values - np.max(values)
    exp = np.exp(shifted)
    total = np.sum(exp)
    probs = exp / total if total > 0 else np.full_like(values, 1.0 / len(values))
    return dict(zip(scores.keys(), (float(p) for p in probs)))
