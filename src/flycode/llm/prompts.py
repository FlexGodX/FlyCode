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

Tool arguments:
  read_file: path; list_files: path (optional); search: pattern, path (optional).
  edit_file: path, old_text, new_text; create_file: path, content.
  git_status, git_diff: no arguments.
  run_tests: target (optional), service (optional Compose service).
  docker_compose: action (up/down/ps/restart/stop/build), service (optional).
    up rebuilds images and waits for service health; run it again after edits.
  docker_logs: service, tail (optional).
  postgres_status: service (optional PostgreSQL Compose service), or
    host/port/user/password/database for a direct connection.
  postgres_query: query, host/port/user/password/database (optional).
  alembic: action, revision (default head), service (optional Compose service).
  http_request: url, method (optional); service_health: url; stop: no arguments.

Inspect README and configuration before choosing service names or URLs.
Use a Compose service for tests and migrations when it supplies the runtime
dependencies and environment variables. Diagnose failures using tool output.
Propose stop only after tests pass following the last edit and all required
Docker, database, migration and service URL checks in Observation succeed.
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
