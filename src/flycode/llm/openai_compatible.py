"""Production LLM cortex - any OpenAI-compatible chat completion endpoint.

Implements the same `LLMCortex` protocol as `HeuristicCandidateGenerator` so
the orchestrator and CLI can select between them with `--llm heuristic|openai`
without any other code change.
"""

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

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
        timeout: float = 60.0,
        max_tokens: int | None = None,
        structured_output: bool = False,
        request_executor: Callable[[Callable[[], Any]], Any] | None = None,
        context_limit: int | None = None,
    ) -> None:
        from openai import DefaultHttpxClient, OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0,
                              http_client=DefaultHttpxClient(trust_env=False) if structured_output else None)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.structured_output = structured_output
        self.request_count = 0
        self.request_executor = request_executor
        if context_limit is not None and context_limit <= 0:
            raise ValueError('Context limit must be positive')
        self.context_limit = context_limit

    def _complete(self, **options):
        if self.context_limit is not None:
            options.setdefault('max_tokens', self.max_tokens or 1536)
            encoded = json.dumps(options['messages'], ensure_ascii=False).encode()
            if len(encoded) + options['max_tokens'] > self.context_limit:
                raise ValueError('context_limit: bounded UTF-8 request plus output exceeds the profile limit')
        def request():
            self.request_count += 1
            return self._client.chat.completions.create(**options)
        return self.request_executor(request) if self.request_executor is not None else request()

    def _candidate_messages(self, context: CortexContext) -> list[dict[str, str]]:
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
        if self.structured_output:
            system += '\nReturn an object with a "candidates" array instead of a bare array.'
            system += ("\nPropose independent alternatives for ONE immediate tool call, not a sequential plan."
                       " Keep each rationale under 15 words. Reuse known file contents from working memory."
                       " Do not repeat a successful read or failed tests unless relevant files changed.")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def context_fits(self, context: CortexContext) -> bool:
        """Use the actual candidate prompt and the existing conservative guard."""
        if self.context_limit is None:
            return True
        size = len(json.dumps(self._candidate_messages(context), ensure_ascii=False).encode())
        return size + (self.max_tokens if self.max_tokens is not None else 1536) <= self.context_limit

    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]:
        n = context.n_candidates or DEFAULT_CANDIDATE_COUNT
        tools = context.candidate_tools or list(VALID_TOOLS)
        options: dict[str, Any] = {}
        if self.max_tokens is not None:
            options["max_tokens"] = self.max_tokens
        if self.structured_output:
            options["response_format"] = _candidate_format(tools, n)
            options["reasoning_effort"] = "none"
        response = self._complete(
            model=self.model,
            temperature=self.temperature,
            messages=self._candidate_messages(context),
            **options,
        )
        raw = response.choices[0].message.content or "[]"
        return _parse_candidates(raw, tools)[:n]

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str:
        prompt = CONSOLIDATION_PROMPT.format(task=context.task, transcript="\n".join(transcript))
        response = self._complete(
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
    if isinstance(items, dict):
        items = items.get("candidates")
    if not isinstance(items, list):
        return []

    candidates: list[CandidateAction] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if tool not in valid_tools:
            continue
        try:
            candidate = CandidateAction(
                id=f"llm-{i}",
                tool=tool,
                arguments=item.get("arguments", {}) or {},
                rationale=item.get("rationale", ""),
                expected_progress=item.get("expected_progress", 0.5),
                expected_information_gain=item.get("expected_information_gain", 0.3),
                expected_risk=item.get("expected_risk", 0.1),
                confidence=item.get("confidence", 0.6),
                source="llm",
            )
        except ValidationError:
            continue
        candidates.append(candidate)
    return candidates


def _candidate_format(tools: list[str], n: int) -> dict:
    properties = {
        "tool": {"type": "string", "enum": tools},
        "arguments": {"type": "object"},
        "rationale": {"type": "string"},
        **{key: {"type": "number", "minimum": 0, "maximum": 1} for key in
           ("expected_progress", "expected_information_gain", "expected_risk", "confidence")},
    }
    return {"type": "json_schema", "json_schema": {"name": "candidate_actions", "strict": True, "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {"candidates": {"type": "array", "minItems": 1, "maxItems": n,
                                      "items": {"type": "object", "properties": properties,
                                                "required": list(properties), "additionalProperties": False}}},
        "required": ["candidates"],
    }}}
