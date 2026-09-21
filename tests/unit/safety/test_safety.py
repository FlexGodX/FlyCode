"""Unit tests for the risk reflex and safety mask (plan §29-30)."""

from flycode.domain import CandidateAction
from flycode.safety import apply_safety_mask, assess_risk, filter_safe


def test_drop_database_is_high_risk_and_blocked():
    action = CandidateAction(id="1", tool="postgres_query", arguments={"query": "DROP DATABASE app;"})
    assessment = assess_risk(action)
    assert assessment.is_blocked
    assert assessment.risk_level >= 0.7


def test_rm_rf_is_blocked():
    action = CandidateAction(id="1", tool="docker_compose", arguments={"cmd": "rm -rf /data"})
    assessment = assess_risk(action)
    assert assessment.is_blocked


def test_ordinary_read_is_not_blocked():
    action = CandidateAction(id="1", tool="read_file", arguments={"path": "a.py"})
    assessment = assess_risk(action)
    assert not assessment.is_blocked
    assert assessment.risk_level < 0.7


def test_safety_mask_filters_high_risk_only():
    candidates = [
        CandidateAction(id="safe", tool="read_file", arguments={"path": "a.py"}),
        CandidateAction(id="unsafe", tool="postgres_query", arguments={"query": "DELETE FROM users;"}),
    ]
    decision = apply_safety_mask(candidates)
    allowed_ids = {c.id for c in decision.allowed_candidates}
    assert "safe" in allowed_ids
    assert "unsafe" not in allowed_ids
    assert len(decision.blocked_candidates) == 1
    assert decision.blocked_candidates[0].candidate.safety_reason


def test_filter_safe_convenience_wrapper_matches_mask():
    candidates = [
        CandidateAction(id="safe", tool="read_file", arguments={"path": "a.py"}),
        CandidateAction(id="unsafe", tool="git_status", arguments={"cmd": "git push --force"}),
    ]
    safe = filter_safe(candidates)
    assert [c.id for c in safe] == ["safe"]
