"""Orchestrator - the online/fast-path agent loop (plan §2-3, §23-28).

Ties together the pieces built for phases 0-3: a `BrainBackend` biases
`BehaviorMode` and per-tool preference, an `LLMCortex` proposes concrete
candidates, the policy layer (safety mask + bias/hard control) picks a
winner, and `SoftwareEnvironment` executes it. This module intentionally
contains no brain- or LLM-specific logic - it only wires the interfaces
together, so any registered backend/cortex combination can be dropped in.
"""

import math
import time
from pathlib import Path

from pydantic import BaseModel, Field

from .brain.protocol import BrainBackend, make_stimulus
from .domain import DEFAULT_CANDIDATE_COUNT, VALID_TOOLS, BehaviorMode, CandidateAction, TaskSpec, ToolAction
from .environment import SoftwareEnvironment
from .llm.protocol import CortexContext, LLMCortex
from .memory import Episode, EpisodicMemory, WorkingMemory, consolidate
from .policy import BehaviorArbiter, ControlMode, run_tournament
from .telemetry import RunMetrics, TelemetryLogger, TrainingStep
from .cognition.recovery import RecoveryState

MAX_WORKING_MEMORY_CHARS = 4000


class RunResult(BaseModel):
    success: bool
    steps: int
    tool_calls: int
    episode: Episode
    metrics: RunMetrics
    transcript: list[str] = Field(default_factory=list)


async def run_agent(
    env: SoftwareEnvironment,
    brain: BrainBackend,
    cortex: LLMCortex,
    task: TaskSpec,
    control_mode: ControlMode = ControlMode.BIAS_CONTROL,
    episodic_memory: EpisodicMemory | None = None,
    telemetry: TelemetryLogger | None = None,
    run_id: str = "run",
    recovery: bool = True,  # Enable recovery reflex
) -> RunResult:
    """Drive one task to completion (or until the step/tool-call budget runs out)."""
    obs = await env.reset(task)
    brain.reset()
    if hasattr(cortex, "reset"):
        cortex.reset()
    arbiter = BehaviorArbiter()
    recovery_state = RecoveryState() if recovery else None
    working_memory = WorkingMemory()
    episodic_memory = episodic_memory or EpisodicMemory()

    transcript: list[str] = []
    actions_log: list[dict] = []
    failures_log: list[dict] = []
    lessons = episodic_memory.recall_lessons(task.task)

    llm_calls = 0
    model_errors: list[str] = []
    regressions = 0
    recovery_events = 0
    unsafe_proposals = 0
    repeated_actions = 0
    rpe_values: list[float] = []
    neural_state = None
    start = time.monotonic()

    changed_lines = 0
    candidate_entropies: list[float] = []
    seen_signatures = {obs.token_signature()}
    latency_total_ms = 0.0
    latency_steps = 0

    for _ in range(task.max_steps):
        if obs.tool_calls_used >= task.max_tool_calls:
            break

        stimulus = make_stimulus(obs, behavior_mode=arbiter.current.value)
        step_start = time.perf_counter()
        neural_state = brain.step(stimulus)
        latency_total_ms += (time.perf_counter() - step_start) * 1000.0
        latency_steps += 1
        rpe_values.append(neural_state.update_weight)
        arbiter.observe(neural_state.behavior_mode)
        mode = arbiter.current

        # Recovery reflex (plan §31): a critical failure overrides the
        # arbiter's mode without waiting for the LLM/controller.
        if recovery_state is not None:
            recovery_state.evaluate(obs)
            recommended = recovery_state.get_recommended_mode()
            if recommended is not None:
                mode = recommended

        if mode is BehaviorMode.RECOVER:
            recovery_events += 1

        context = CortexContext(
            task=task.task,
            behavior_mode=mode.value,
            observation=obs,
            working_memory=working_memory.as_list(),
            episodic_notes=lessons,
            fly_policy_bias=neural_state.action_bias,
            candidate_tools=list(VALID_TOOLS),
            n_candidates=DEFAULT_CANDIDATE_COUNT,
            neural_state=neural_state,
            control_mode=control_mode.value,
        )
        candidates = cortex.generate_candidates(context)
        llm_calls += 1
        routing = cortex.last_routing.model_dump() if hasattr(cortex, "last_routing") else None
        if routing is not None:
            transcript.append(f"[models] route={routing['route']} reason={routing['reason']} error={routing['error']}")
            if routing["error"]:
                model_errors.append(routing["error"])

        if mode in (BehaviorMode.CONSOLIDATE, BehaviorMode.STOP) and not candidates:
            candidates = [CandidateAction(id="auto-stop", tool="stop", rationale="controller reached STOP")]
        if not candidates:
            transcript.append(f"[{mode.value}] no candidates proposed; stopping")
            break

        fly_bias = _fly_bias_for(brain, candidates, mode.value, neural_state.action_bias)
        result = run_tournament(candidates, fly_bias, control_mode)
        unsafe_proposals += len(result.blocked)
        candidate_entropies.append(_score_entropy(result.scores))

        if result.winner is None:
            transcript.append(f"[{mode.value}] all candidates blocked by safety; stopping")
            break

        winner = result.winner
        if winner.tool == "stop" and not obs.is_healthy:
            transcript.append(f"[{mode.value}] stop rejected: completion requires verification")
            break
        action = ToolAction(
            id=winner.id,
            tool=winner.tool,
            arguments=winner.arguments,
            rationale=winner.rationale,
            expected_risk=winner.expected_risk,
            expected_cost=winner.expected_cost,
            source=winner.source,
        )

        if hasattr(brain, "habituation"):
            streak_before = brain.habituation.repetition_streak(action, mode.value)
            if streak_before > 0:
                repeated_actions += 1

        transition = await env.step(action)
        obs = transition.current
        seen_signatures.add(obs.token_signature())
        changed_lines += _changed_lines(action, transition)

        if transition.is_regression:
            regressions += 1

        _update_working_memory(working_memory, action, transition)
        actions_log.append({"tool": action.tool, "arguments": action.arguments, "mode": mode.value})
        if not transition.success:
            failures_log.append({"tool": action.tool, "error": transition.error_message})

        if hasattr(brain, "record_outcome"):
            brain.record_outcome(action, mode.value, transition.utility, transition.success)

        transcript.append(f"[{mode.value}] {action.tool}({action.arguments}) -> success={transition.success}")

        if telemetry is not None:
            telemetry.log_step(
                run_id,
                TrainingStep(
                    observation=transition.previous.model_dump(),
                    neural_state=neural_state.state_vector,
                    candidates=[c.model_dump() for c in candidates],
                    chosen_action=action.model_dump(),
                    outcome=transition.model_dump(exclude={"previous", "current"}),
                    reward_prediction_error=neural_state.update_weight,
                    future_return=transition.reward,
                    model_routing=routing,
                ),
            )

        if action.tool == "stop" or env.is_done():
            break

    episode = consolidate(
        cortex,
        task.task,
        obs,
        transcript,
        working_memory.as_list(),
        actions_log,
        failures_log,
    )
    episodic_memory.add(episode)

    model_calls = dict(getattr(cortex, "model_calls", {}))
    metrics = RunMetrics(
        task_id=task.id,
        brain=brain.name,
        control_mode=control_mode.value,
        success=obs.is_healthy,
        verified_completion=obs.is_healthy,
        steps=obs.step_number,
        tool_calls=obs.tool_calls_used,
        llm_calls=sum(model_calls.values()) if model_calls else llm_calls,
        model_calls=model_calls,
        model_errors=model_errors,
        wall_time_s=time.monotonic() - start,
        changed_lines=changed_lines,
        regressions=regressions,
        recovery_events=recovery_events,
        unsafe_proposals=unsafe_proposals,
        repeated_actions=repeated_actions,
        novel_observations=len(seen_signatures),
        strategy_switches=arbiter.strategy_switches,
        final_confidence=neural_state.confidence if neural_state is not None else 0.0,
        mean_candidate_entropy=sum(candidate_entropies) / len(candidate_entropies) if candidate_entropies else 0.0,
        mean_rpe=sum(rpe_values) / len(rpe_values) if rpe_values else 0.0,
    )
    if latency_steps:
        # Measured mean step latency (plan §62 matched-compute). Backends that
        # expose latency_ms as a read-only property keep their own estimate.
        try:
            brain.latency_ms = latency_total_ms / latency_steps
        except AttributeError:
            pass
    if telemetry is not None:
        telemetry.log_run(metrics)

    return RunResult(
        success=metrics.success,
        steps=metrics.steps,
        tool_calls=metrics.tool_calls,
        episode=episode,
        metrics=metrics,
        transcript=transcript,
    )


def _fly_bias_for(
    brain: BrainBackend,
    candidates: list[CandidateAction],
    mode: str,
    tool_bias: dict[str, float],
) -> dict[str, float]:
    if hasattr(brain, "score_candidates"):
        return brain.score_candidates(candidates, mode)
    return {c.id: tool_bias.get(c.tool, 0.0) for c in candidates}


def _update_working_memory(memory: WorkingMemory, action: ToolAction, transition) -> None:
    if action.tool == "read_file":
        content = transition.payload.get("content", "")[:MAX_WORKING_MEMORY_CHARS]
        path = action.arguments.get("path", "?")
        memory.add(f"read_file({path}):\n{content}")
    elif action.tool == "run_tests":
        memory.add(f"run_tests stdout:\n{transition.stdout[-MAX_WORKING_MEMORY_CHARS:]}")
    elif action.tool in ("edit_file", "create_file"):
        path = action.arguments.get("path", "?")
        if transition.success:
            prefix = f"read_file({path}):\n"
            memory.facts = [fact for fact in memory.facts if not fact.startswith(prefix)]
        status = "ok" if transition.success else f"FAILED: {transition.error_message}"
        memory.add(f"{action.tool}({path}): {status}")
    elif not transition.success and transition.error_message:
        memory.add(f"{action.tool} failed: {transition.error_message}")
    if action.tool not in ("read_file", "run_tests"):
        if transition.stdout:
            memory.add(f"{action.tool} stdout:\n{transition.stdout[-MAX_WORKING_MEMORY_CHARS:]}")
        if transition.stderr:
            memory.add(f"{action.tool} stderr:\n{transition.stderr[-MAX_WORKING_MEMORY_CHARS:]}")
        if transition.payload:
            memory.add(f"{action.tool} result: {str(transition.payload)[:MAX_WORKING_MEMORY_CHARS]}")


def _score_entropy(scores: dict[str, float]) -> float:
    """Shannon entropy (nats) of the softmax over final candidate scores.

    Plan §66 "candidate entropy": how spread out the tournament's preferences
    are. 0.0 means one candidate dominates; high values mean near-uniform.
    """
    if len(scores) < 2:
        return 0.0
    peak = max(scores.values())
    weights = [math.exp(s - peak) for s in scores.values()]
    total = sum(weights)
    return -sum((w / total) * math.log(w / total) for w in weights)


def _changed_lines(action: ToolAction, transition) -> int:
    """Lines added+removed by a successful edit/create (plan §66 "changed lines")."""
    if not transition.success:
        return 0
    if action.tool == "edit_file":
        old = str(action.arguments.get("old_text", ""))
        new = str(action.arguments.get("new_text", ""))
        return old.count("\n") + new.count("\n") + (2 if old or new else 0)
    if action.tool == "create_file":
        content = str(action.arguments.get("content", ""))
        return content.count("\n") + (1 if content else 0)
    return 0

