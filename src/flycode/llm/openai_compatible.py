"""Production LLM cortex - any OpenAI-compatible chat completion endpoint.

Implements the same `LLMCortex` protocol as `HeuristicCandidateGenerator` so
the orchestrator and CLI can select between them with `--llm heuristic|openai`
without any other code change.
"""

import json

from ..domain import DEFAULT_CANDIDATE_COUNT, VALID_TOOLS, CandidateAction
from .prompts import CONSOLIDATION_PROMPT, SYSTEM_PROMPT, render_user_prompt
from .protocol import CortexContext


class OpenAICompatibleLLM:
    """Chat-completion-backed cortex. Requires the `openai` package and a key."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
    ) -> None:
        from openai import OpenAI  # imported lazily so the heuristic path needs no network deps

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]:
        n = context.n_candidates or DEFAULT_CANDIDATE_COUNT
        tools = context.candidate_tools or list(VALID_TOOLS)
        system = SYSTEM_PROMPT.format(n_candidates=n, tools=", ".join(tools))
        user = render_user_prompt(
            task=context.task,
            behavior_mode=context.behavior_mode,
            working_memory=context.working_memory,
            episodic_notes=context.episodic_notes,
            fly_policy_bias=context.fly_policy_bias,
            observation_summary=context.observation.model_dump_json(),
        )

        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        raw = response.choices[0].message.content or "[]"
        return _parse_candidates(raw, tools)

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str:
        prompt = CONSOLIDATION_PROMPT.format(task=context.task, transcript="\n".join(transcript))
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


def _parse_candidates(raw: str, valid_tools: list[str]) -> list[CandidateAction]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []

    candidates: list[CandidateAction] = []
    for i, item in enumerate(items):
        tool = item.get("tool")
        if tool not in valid_tools:
            continue
        candidates.append(
            CandidateAction(
                id=f"llm-{i}",
                tool=tool,
                arguments=item.get("arguments", {}) or {},
                rationale=item.get("rationale", ""),
                expected_progress=float(item.get("expected_progress", 0.5)),
                expected_information_gain=float(item.get("expected_information_gain", 0.3)),
                expected_risk=float(item.get("expected_risk", 0.1)),
                confidence=float(item.get("confidence", 0.6)),
                source="llm",
            )
        )
    return candidates
