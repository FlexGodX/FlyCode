"""Neural interface types shared by every brain backend (plan §5)."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .observation import Observation


class NeuralStimulus(BaseModel):
    """Sensory input to the neural controller."""

    model_config = ConfigDict(extra="allow")

    observation: Observation
    target_behavior: str = "execute"
    behavior_mode: str = "explore"
    intensity: float = Field(default=1.0, ge=0.0, le=2.0)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)

    # Candidate actions offered to the arbiter (optional)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class NeuralState(BaseModel):
    """Output state of a brain backend after one step."""

    model_config = ConfigDict(extra="allow")

    state_vector: list[float] = Field(default_factory=list)

    # Central-Complex-like task compass readout
    task_direction: list[float] = Field(default_factory=list)
    deviation: float = Field(default=0.0, ge=0.0, le=1.0)
    persistence: float = Field(default=0.0, ge=0.0, le=1.0)

    # Modulatory scalars
    exploration_bias: float = Field(default=0.0, ge=0.0, le=1.0)
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Mushroom-body-like memory readout
    active_odes: list[int] = Field(default_factory=list)
    recent_actions: list[str] = Field(default_factory=list)

    # Safety / learning
    risk_level: float = Field(default=0.0, ge=0.0, le=1.0)
    is_blocked: bool = False
    is_learning: bool = False
    update_weight: float = Field(default=0.0, ge=0.0, le=1.0)

    # Controller output
    behavior_mode: str = "explore"
    mode_probs: dict[str, float] = Field(default_factory=dict)
    action_bias: dict[str, float] = Field(default_factory=dict)

    @property
    def dimensions(self) -> int:
        return len(self.state_vector)

    @property
    def size_bytes(self) -> int:
        return self.dimensions * 4  # float32 assumption

    def bias_for(self, tool: str, default: float = 0.0) -> float:
        """Look up the bias for a tool name."""
        return float(self.action_bias.get(tool, default))