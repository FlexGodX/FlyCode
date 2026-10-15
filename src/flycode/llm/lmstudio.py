"""Two local models scheduled by the current neural controller (§3,23–27)."""

import os

from openai import APIError
from pydantic import BaseModel, Field

from ..domain import DEFAULT_CANDIDATE_COUNT, VALID_TOOLS, CandidateAction
from ..policy.model_routing import plan_model_calls
from ..safety import filter_safe
from .decision import JevStyleDecisionClient
from .openai_compatible import OpenAICompatibleLLM
from .protocol import CortexContext

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_REASONER = "qwen/qwen3.5-9b"
DEFAULT_DECIDER = "jev-style-qwen3.5-2b-decision"


class ModelRoutingTrace(BaseModel):
    route: str = "none"
    reason: str = ""
    error: str = ""
    decision_scores: dict[str, float] = Field(default_factory=dict)
    model_calls: dict[str, int] = Field(default_factory=dict)


class LMStudioCortex:
    """Qwen proposes, Jev evaluates choices, and the brain retains final control."""

    def __init__(self, reasoner: OpenAICompatibleLLM, decider: JevStyleDecisionClient,
                 max_model_calls: int = 60) -> None:
        if max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        self.reasoner = reasoner
        self.decider = decider
        self.max_model_calls = max_model_calls
        self.reset()

    @property
    def model_calls(self) -> dict[str, int]:
        return {self.reasoner.model: self.reasoner.request_count, self.decider.model: self.decider.request_count}

    def reset(self) -> None:
        self.reasoner.request_count = 0
        self.decider.request_count = 0
        self.last_routing = ModelRoutingTrace()

    def _has_budget(self) -> bool:
        return sum(self.model_calls.values()) < self.max_model_calls

    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]:
        plan = plan_model_calls(context)
        trace = self.last_routing = ModelRoutingTrace(reason=plan.reason)
        if plan.local_candidate is not None:
            trace.route = "local"
            trace.model_calls = self.model_calls
            tools = context.candidate_tools or list(VALID_TOOLS)
            return [plan.local_candidate] if plan.local_candidate.tool in tools else []
        if not plan.call_reasoner:
            trace.route = "blocked"
            trace.model_calls = self.model_calls
            return []
        if not self._has_budget():
            trace.error = "model-call budget exhausted"
            trace.model_calls = self.model_calls
            return []

        trace.route = "reasoner"
        try:
            candidates = self.reasoner.generate_candidates(context)[:DEFAULT_CANDIDATE_COUNT]
        except (APIError, ValueError) as exc:
            trace.error = f"reasoner request failed: {type(exc).__name__}"
            trace.model_calls = self.model_calls
            return []
        if not candidates:
            trace.error = "reasoner returned no valid candidates"
        safe = filter_safe(candidates)
        if plan.call_decider and len(safe) > 1:
            if self._has_budget():
                trace.route = "reasoner+decider"
                try:
                    trace.decision_scores = self.decider.score(context, safe)
                    candidates = [candidate.model_copy(update={"rank_score": trace.decision_scores[candidate.id]})
                                  if candidate.id in trace.decision_scores else candidate for candidate in candidates]
                except (APIError, ValueError) as exc:
                    trace.error = f"decider request failed: {type(exc).__name__}; retaining reasoner proposals"
            else:
                trace.error = "decision budget exhausted; retaining reasoner proposals"
        trace.model_calls = self.model_calls
        return candidates

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str:
        outcome = "verified" if context.observation.is_healthy else "not verified"
        return (f"Task '{context.task}': {outcome}; {context.observation.step_number} tool steps; "
                f"model requests: {sum(self.model_calls.values())}.")


def make_lmstudio_cortex(base_url: str | None = None, model: str = DEFAULT_REASONER,
                         decision_model: str = DEFAULT_DECIDER, timeout: float = 120.0,
                         max_model_calls: int = 60) -> LMStudioCortex:
    if timeout <= 0:
        raise ValueError("request timeout must be positive")
    if model == decision_model:
        raise ValueError("reasoner and decider require distinct model IDs")
    base_url = base_url or os.environ.get("LMSTUDIO_BASE_URL") or DEFAULT_BASE_URL
    key = os.environ.get("LMSTUDIO_API_KEY") or "lm-studio"
    reasoner = OpenAICompatibleLLM(model=model, base_url=base_url, api_key=key, temperature=0,
                                   structured_output=True, max_tokens=1536, timeout=timeout)
    available = {item.id for item in reasoner._client.models.list()}
    missing = {model, decision_model} - available
    if missing:
        raise ValueError(f"Models not available in LM Studio: {sorted(missing)}. Available IDs: {sorted(available)}")
    decider = JevStyleDecisionClient(model=decision_model, base_url=base_url, api_key=key, timeout=timeout)
    return LMStudioCortex(reasoner, decider, max_model_calls=max_model_calls)
