"""Explicit multi-candidate diagnostic probe, separate from the default cortex."""

from ..domain import CandidateAction
from ..llm import HeuristicCandidateGenerator
from ..llm.protocol import CortexContext


class TournamentCortex(HeuristicCandidateGenerator):
    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]:
        candidates = super().generate_candidates(context)
        if context.behavior_mode != 'execute':
            return candidates
        for tool, reason in [('run_tests', 'recheck current failures'),
                             ('git_diff', 'inspect the current patch before proceeding')]:
            if len(candidates) >= context.n_candidates:
                break
            if not any(candidate.tool == tool and not candidate.arguments for candidate in candidates):
                candidates.append(CandidateAction(id='probe-' + tool, tool=tool, rationale=reason,
                    expected_progress=0., expected_information_gain=.1, confidence=.5))
        return candidates
