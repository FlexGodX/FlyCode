"""LLM Cortex - semantic reasoning layer (plan §23-26)."""

from .candidate_generator import HeuristicCandidateGenerator
from .protocol import CortexContext, LLMCortex

__all__ = ["LLMCortex", "CortexContext", "HeuristicCandidateGenerator"]


def make_openai_llm(*args, **kwargs):
    """Lazy factory so `openai` is only imported when actually requested."""
    from .openai_compatible import OpenAICompatibleLLM

    return OpenAICompatibleLLM(*args, **kwargs)
