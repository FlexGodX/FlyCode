"""Prompt templates for the OpenAI-compatible LLM cortex (plan §23)."""

SYSTEM_PROMPT = """You are the semantic reasoning cortex of an autonomous coding agent.
You do not control the final action directly: a fly-derived neural controller
biases and can veto your proposals for safety. Your job is to propose concrete,
small, verifiable tool calls that make progress on the task given the current
BehaviorMode.

Respond ONLY with a JSON array of {n_candidates} candidate actions, each with:
  tool (one of: {tools}), arguments (object), rationale (string),
  expected_progress (0..1), expected_information_gain (0..1),
  expected_risk (0..1), confidence (0..1).
"""

USER_TEMPLATE = """Task: {task}
BehaviorMode: {behavior_mode}

Working memory:
{working_memory}

Recent lessons:
{episodic_notes}

Fly policy bias (positive = encouraged, negative = discouraged):
{fly_policy_bias}

Observation:
{observation}
"""

CONSOLIDATION_PROMPT = """The task is ending. Summarize in a few sentences:
- What happened?
- What helped?
- What did not help?
- Which strategy succeeded?
- What should be remembered for next time?

Task: {task}
Transcript:
{transcript}
"""


def render_user_prompt(
    task: str,
    behavior_mode: str,
    working_memory: list[str],
    episodic_notes: list[str],
    fly_policy_bias: dict[str, float],
    observation_summary: str,
) -> str:
    return USER_TEMPLATE.format(
        task=task,
        behavior_mode=behavior_mode,
        working_memory="\n".join(f"- {m}" for m in working_memory) or "(empty)",
        episodic_notes="\n".join(f"- {n}" for n in episodic_notes) or "(none)",
        fly_policy_bias=fly_policy_bias or "(none)",
        observation=observation_summary,
    )
