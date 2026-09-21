"""Unit tests for the policy layer (plan §26-29)."""

from flycode.domain import BehaviorMode, CandidateAction
from flycode.policy import (
    BehaviorArbiter,
    ControlMode,
    rank_candidates,
    run_tournament,
    select_candidate,
    select_llm_only,
)


def make_candidates():
    return [
        CandidateAction(id="1", tool="read_file", arguments={"path": "a.py"}, expected_progress=0.3, confidence=0.6),
        CandidateAction(id="2", tool="edit_file", arguments={"path": "a.py"}, expected_progress=0.8, confidence=0.7),
        CandidateAction(
            id="3",
            tool="postgres_query",
            arguments={"query": "DROP DATABASE app;"},
            expected_progress=0.9,
            expected_risk=0.95,
        ),
    ]


class TestBiasControl:
    def test_fly_bias_shifts_winner(self):
        candidates = make_candidates()
        result = rank_candidates(candidates, fly_bias={"read_file": 5.0})
        assert result.winner.id == "1"

    def test_safety_blocks_high_risk_winner(self):
        candidates = make_candidates()
        result = rank_candidates(candidates, fly_bias={"postgres_query": 10.0})
        assert result.winner is not None
        assert result.winner.tool != "postgres_query"

    def test_per_candidate_id_bias_takes_precedence(self):
        candidates = make_candidates()
        result = rank_candidates(candidates, fly_bias={"1": 5.0, "read_file": -5.0})
        assert result.winner.id == "1"


class TestHardControl:
    def test_hard_control_ignores_llm_score(self):
        candidates = make_candidates()
        result = select_candidate(candidates, fly_scores={"1": 0.1, "2": 0.9})
        assert result.winner.id == "2"

    def test_hard_control_respects_safety_mask(self):
        candidates = make_candidates()
        result = select_candidate(candidates, fly_scores={"3": 100.0, "1": 0.1, "2": 0.2})
        assert result.winner.id == "2"


class TestCandidateTournament:
    def test_dispatches_bias_vs_hard(self):
        candidates = make_candidates()
        bias_result = run_tournament(candidates, {"read_file": 5.0}, ControlMode.BIAS_CONTROL)
        hard_result = run_tournament(candidates, {"1": 0.1, "2": 0.9}, ControlMode.HARD_CONTROL)
        assert bias_result.winner.id == "1"
        assert hard_result.winner.id == "2"

    def test_empty_candidates_returns_no_winner(self):
        result = run_tournament([], {}, ControlMode.BIAS_CONTROL)
        assert result.winner is None


class TestLLMOnlyBaseline:
    def test_no_fly_bias_applied(self):
        candidates = make_candidates()
        result = select_llm_only(candidates)
        assert result.ranked[0].fly_bias == 0.0


class TestBehaviorArbiter:
    def test_tracks_strategy_switches(self):
        arbiter = BehaviorArbiter()
        arbiter.observe("explore")
        arbiter.observe("diagnose")
        arbiter.observe("diagnose")
        arbiter.observe("execute")
        assert arbiter.strategy_switches == 2  # explore->diagnose, diagnose->execute
        assert arbiter.current is BehaviorMode.EXECUTE

    def test_flags_unexpected_jump(self):
        arbiter = BehaviorArbiter()
        entry = arbiter.observe("stop")  # explore -> stop is not in the soft prior
        assert entry.expected is False
