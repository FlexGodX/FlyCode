"""Consolidation - builds an Episode from a finished run (plan §34).

Runs at CONSOLIDATE, right before STOP. The LLM cortex is asked to summarize
what happened; this module packages that summary together with the raw
transcript into the `Episode` that `EpisodicMemory` persists.
"""

from ..domain import Observation
from ..llm.protocol import CortexContext, LLMCortex
from .episodic import Episode


def consolidate(
    cortex: LLMCortex,
    task: str,
    observation: Observation,
    transcript: list[str],
    working_memory: list[str],
    actions: list[dict],
    failures: list[dict],
) -> Episode:
    context = CortexContext(task=task, behavior_mode="consolidate", observation=observation, working_memory=working_memory)
    summary = cortex.consolidate(context, transcript)

    lessons = [line.strip("- ").strip() for line in summary.splitlines() if line.strip()]
    return Episode(
        task=task,
        key_observations=working_memory[-10:],
        actions=actions,
        failures=failures,
        final_result=summary,
        lessons=lessons or [summary],
    )
