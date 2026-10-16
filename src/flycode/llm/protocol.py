"""LLM Cortex interface (plan §23-25).

The LLM is the semantic reasoner: it sees the task, behavior mode, compass
state, memory and observation, and proposes concrete candidate actions. This
module defines the structural contract so the orchestrator can swap a real
model (`openai_compatible.py`) for a deterministic stand-in
(`candidate_generator.py`) in tests and offline demos without changing any
calling code.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..domain import CandidateAction, NeuralState, Observation


class CortexContext(BaseModel):
    """Everything the LLM cortex is given for one decision (plan §23)."""

    task: str
    behavior_mode: str
    observation: Observation
    working_memory: list[str] = Field(default_factory=list)
    episodic_notes: list[str] = Field(default_factory=list)
    fly_policy_bias: dict[str, float] = Field(default_factory=dict)
    candidate_tools: list[str] = Field(default_factory=list)
    artifact_reads: bool = False
    n_candidates: int = 4
    neural_state: NeuralState | None = None
    control_mode: str = "bias_control"


@runtime_checkable
class LLMCortex(Protocol):
    """Structural protocol for anything that can propose candidate actions."""

    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]: ...

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str: ...
