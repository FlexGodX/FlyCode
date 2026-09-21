"""Heuristic finite-state-machine baseline controller.

No learning, no persistent value estimate: pure hand-written rules. This is
the cheapest baseline and defines the floor that fly-derived controllers must
beat to be interesting.
"""

from ..domain import NeuralState, NeuralStimulus
from .protocol import BrainBackend

TOOLS = ("read_file", "list_files", "search", "git_status", "git_diff",
         "run_tests", "edit_file", "create_file", "docker_compose",
         "docker_logs", "postgres_status", "alembic", "http_request",
         "service_health", "stop")

STAGE_ORDER = ("explore", "diagnose", "plan", "execute", "verify", "consolidate", "stop")


class HeuristicFSMBackend(BrainBackend):
    """Deterministic rule-based controller walking a fixed workflow.

    Workflow: explore -> diagnose -> plan -> execute -> verify -> (recover) -> consolidate -> stop
    """

    name = "heuristic_fsm"
    tier = 0

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self.reset()

    # ------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._steps = 0
        self.stage_index = 0
        self.recoveries = 0
        self.last_tests_status = "unknown"
        self.reads_in_stage = 0
        self._state = NeuralState(behavior_mode="explore", action_bias={"read_file": 0.8})

    def step(self, stimulus: NeuralStimulus) -> NeuralState:
        obs = stimulus.observation
        self._steps += 1

        failed = obs.tests_status == "failed" or obs.build_status == "failed" or bool(obs.recent_errors)
        tests_passed = obs.tests_status == "passed"

        # Regression -> force recovery
        if failed and self.last_tests_status == "passed":
            self.stage_index = STAGE_ORDER.index("recover")
            self.recoveries += 1

        self.last_tests_status = obs.tests_status

        stage = STAGE_ORDER[min(self.stage_index, len(STAGE_ORDER) - 1)]

        if stage == "explore":
            self.reads_in_stage += 1
            if self.reads_in_stage >= 2:
                self.stage_index += 1
                self.reads_in_stage = 0
        elif stage == "diagnose":
            if tests_passed or self.reads_in_stage >= 2:
                self.stage_index += 1
                self.reads_in_stage = 0
            else:
                self.reads_in_stage += 1
        elif stage == "plan":
            self.stage_index += 1
            self.reads_in_stage = 0
        elif stage == "execute":
            # Advance to verify after a bounded number of attempts regardless
            # of `tests_passed`: that flag can only change once we actually
            # re-run the tests, which happens in verify, not here - waiting
            # for it inside execute would deadlock.
            self.reads_in_stage += 1
            if tests_passed or self.reads_in_stage >= 2:
                self.stage_index += 1
                self.reads_in_stage = 0
        elif stage == "verify":
            if tests_passed:
                self.stage_index += 1
                self.reads_in_stage = 0
            else:
                self.stage_index = STAGE_ORDER.index("recover")
        elif stage == "recover":
            if tests_passed:
                self.stage_index = STAGE_ORDER.index("verify")
            self.reads_in_stage += 1
        elif stage == "consolidate":
            self.stage_index += 1

        stage = STAGE_ORDER[min(self.stage_index, len(STAGE_ORDER) - 1)]

        action_bias = self._bias_for_stage(stage, obs)
        self._state = NeuralState(
            state_vector=[float(self.stage_index), float(self.reads_in_stage), float(self.recoveries)],
            behavior_mode=stage,
            action_bias=action_bias,
            confidence=0.6,
            valence=0.2 if tests_passed else -0.3,
            exploration_bias=1.0 if stage == "explore" else 0.0,
        )
        return self._state

    def get_state(self) -> NeuralState:
        return self._state

    def close(self) -> None:
        pass

    # ------------------------------------------------------------ rules
    def _bias_for_stage(self, stage: str, obs) -> dict[str, float]:
        table = {
            "explore": {"read_file": 0.9, "list_files": 0.7, "search": 0.5, "git_status": 0.4},
            "diagnose": {"run_tests": 0.9, "git_diff": 0.6, "read_file": 0.5, "docker_logs": 0.3},
            "plan": {"read_file": 0.5, "search": 0.4, "list_files": 0.3},
            "execute": {"edit_file": 0.9, "create_file": 0.5, "docker_compose": 0.4},
            "verify": {"run_tests": 0.9, "http_request": 0.5, "service_health": 0.5, "git_diff": 0.3},
            "recover": {"run_tests": 0.8, "git_diff": 0.6, "edit_file": 0.6, "docker_compose": 0.5},
            "consolidate": {"stop": 0.9, "git_diff": 0.3},
            "stop": {"stop": 1.0},
        }
        bias = table.get(stage, {"read_file": 0.5})
        if obs.database.get("available") and not obs.database.get("connected"):
            bias = dict(bias)
            bias["postgres_status"] = 0.7
            bias["docker_compose"] = max(bias.get("docker_compose", 0.0), 0.6)
        return bias