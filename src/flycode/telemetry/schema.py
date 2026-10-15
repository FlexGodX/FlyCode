"""Telemetry schemas - per-step training records and per-run metrics (plan §53, §66)."""

from pydantic import BaseModel, Field


class TrainingStep(BaseModel):
    """One recorded decision point, suitable for offline imitation/RL (plan §53)."""

    observation: dict
    neural_state: list[float] | None = None
    task_compass_state: dict | None = None
    candidates: list[dict] = Field(default_factory=list)
    chosen_action: dict
    outcome: dict
    reward_prediction_error: float = 0.0
    future_return: float = 0.0
    model_routing: dict | None = None
    shadow_decision: dict | None = None


class RunMetrics(BaseModel):
    """Per-run summary metrics (plan §66)."""

    task_id: str
    brain: str
    control_mode: str
    success: bool = False
    verified_completion: bool = False
    steps: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    model_calls: dict[str, int] = Field(default_factory=dict)
    model_errors: list[str] = Field(default_factory=list)
    wall_time_s: float = 0.0
    changed_lines: int = 0
    regressions: int = 0
    recovery_events: int = 0
    unsafe_proposals: int = 0
    repeated_actions: int = 0
    novel_observations: int = 0
    strategy_switches: int = 0
    final_confidence: float = 0.0
    mean_candidate_entropy: float = 0.0
    mean_rpe: float = 0.0
    online_updates: int = 0
    online_rollbacks: int = 0
    online_learning_ms: float = 0.0
    shadow_decisions: int = 0
    shadow_agreements: int = 0
