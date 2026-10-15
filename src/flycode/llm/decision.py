"""Native Jev-Style v1 option scoring through LM Studio (§23–27).

Protocol: the installed model's card, not the incompatible v3 runtime.
https://huggingface.co/chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-GGUF
"""

import json
import math
import string

from openai import DefaultHttpxClient, OpenAI

from ..domain import CandidateAction
from .protocol import CortexContext


class JevStyleDecisionClient:
    def __init__(self, model: str, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self.model = model
        self.request_count = 0
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0,
                              http_client=DefaultHttpxClient(trust_env=False))

    def score(self, context: CortexContext, candidates: list[CandidateAction]) -> dict[str, float]:
        if not 2 <= len(candidates) <= 20 or len({c.id for c in candidates}) != len(candidates):
            raise ValueError("Jev requires 2–20 uniquely identified candidates")
        state = json.dumps({
            "task": context.task[:1000], "mode": context.behavior_mode,
            "tests": context.observation.tests, "errors": context.observation.recent_errors[-3:],
            "memory": [fact[-1200:] for fact in context.working_memory[-3:]],
            "fly_policy_bias": context.fly_policy_bias,
        }, ensure_ascii=False)
        options = "\n".join(f"{string.ascii_uppercase[i]}. {c.tool} {json.dumps(c.arguments, ensure_ascii=False)}"
                            f" — {c.rationale}" for i, c in enumerate(candidates))
        prompt = ("You are a decision function. Read the state, then answer the question by choosing exactly one option.\n\n"
                  f"[State]\n{state}\n\n[Question]\nWhich action best advances this task safely in the current mode?\n\n"
                  f"[Options]\n{options}\n\nAnswer:")
        self.request_count += 1
        response = self._client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": prompt}],
            max_tokens=1, temperature=0, logprobs=True, top_logprobs=20,
        )
        if not response.choices or not response.choices[0].logprobs or not response.choices[0].logprobs.content:
            raise ValueError("Jev returned no token log probabilities")
        tokens = response.choices[0].logprobs.content[0].top_logprobs
        logits = []
        for i in range(len(candidates)):
            values = [item.logprob for item in tokens if item.token.strip() == string.ascii_uppercase[i]]
            if not values or not all(math.isfinite(value) for value in values):
                raise ValueError("Jev returned an incomplete or nonfinite option distribution")
            # Whitespace variants represent the same declared option.
            logits.append(max(values))
        peak = max(logits)
        weights = [math.exp(value - peak) for value in logits]
        total = sum(weights)
        return {candidate.id: weight / total for candidate, weight in zip(candidates, weights)}
