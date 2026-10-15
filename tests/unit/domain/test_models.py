"""Unit tests for core domain models."""

from flycode.domain import (
    BehaviorMode,
    CandidateAction,
    Observation,
    ObservationDelta,
    TaskSpec,
    ToolAction,
    Transition,
    is_valid_transition,
)


def test_behavior_mode_terminal():
    assert BehaviorMode.STOP.is_terminal
    assert not BehaviorMode.EXPLORE.is_terminal


def test_valid_transition_soft_prior():
    assert is_valid_transition(BehaviorMode.EXPLORE, BehaviorMode.DIAGNOSE)
    assert is_valid_transition(BehaviorMode.EXPLORE, BehaviorMode.EXPLORE)
    assert not is_valid_transition(BehaviorMode.EXPLORE, BehaviorMode.STOP)


def test_task_spec_resource_constraints():
    task = TaskSpec(id="t1", task="fix it", max_tool_calls=15)
    assert task.is_resource_constrained()
    task2 = TaskSpec(id="t2", task="fix it", max_tool_calls=60)
    assert not task2.is_resource_constrained()


def test_observation_health_and_signature():
    obs = Observation(task="x", tests={"status": "passed"}, build={"status": "success"})
    assert obs.is_healthy
    assert obs.failing_tests == 0

    broken = Observation(task="x", tests={"status": "failed", "failed": 2}, recent_errors=["boom"])
    assert not broken.is_healthy
    assert broken.failing_tests == 2
    assert broken.token_signature() != obs.token_signature()


def test_observation_redacted_hides_fields():
    obs = Observation(task="x", tests={"status": "passed"}, recent_errors=["e1"])
    redacted = obs.redacted(["tests", "recent_errors"])
    assert redacted.tests == {"status": "hidden"}
    assert redacted.recent_errors == []
    assert obs.tests == {"status": "passed"}  # original untouched


def test_tool_action_fingerprint_stable():
    a1 = ToolAction(id="1", tool="edit_file", arguments={"path": "x.py"})
    a2 = ToolAction(id="2", tool="edit_file", arguments={"path": "x.py"})
    assert a1.fingerprint("execute") == a2.fingerprint("execute")
    assert a1.is_mutating
    assert not a1.is_read_only


def test_candidate_action_defaults():
    candidate = CandidateAction(id="c1", tool="read_file", arguments={"path": "a.py"})
    assert candidate.fly_bias == 0.0
    assert candidate.rank_score is None


def test_transition_reward_and_regression():
    obs1 = Observation(task="x", tests={"status": "passed", "failed": 0})
    obs2 = Observation(task="x", tests={"status": "failed", "failed": 1})
    action = ToolAction(id="a", tool="edit_file")
    t = Transition(
        previous=obs1,
        action=action,
        current=obs2,
        utility=-0.2,
        aversion=0.1,
        delta=ObservationDelta(new_failing_tests=1),
    )
    assert t.is_regression
    assert t.reward < 0


def test_first_failing_test_run_is_not_a_regression():
    """Discovering pre-existing breakage (unknown -> failed) is not a regression."""
    obs1 = Observation(task="x")  # tests never run: status "unknown"
    obs2 = Observation(task="x", tests={"status": "failed", "failed": 1})
    action = ToolAction(id="a", tool="run_tests")
    t = Transition(
        previous=obs1,
        action=action,
        current=obs2,
        delta=ObservationDelta(new_failing_tests=1),
    )
    assert not t.is_regression


def test_making_a_failing_repo_worse_is_a_regression():
    obs1 = Observation(task="x", tests={"status": "failed", "failed": 1})
    obs2 = Observation(task="x", tests={"status": "failed", "failed": 2})
    action = ToolAction(id="a", tool="edit_file")
    t = Transition(
        previous=obs1,
        action=action,
        current=obs2,
        delta=ObservationDelta(new_failing_tests=1),
    )
    assert t.is_regression



def test_environment_health_requires_all_declared_evidence():
    obs = Observation(
        task="run app", tests={"status": "passed"},
        docker={"required": True, "running": True},
        database={"required": True, "connected": True, "migration_required": True, "migration_applied": True},
        services={"required_urls": ["http://localhost:8000/health", "http://localhost:8000/users"],
                  "checked_urls": {"http://localhost:8000/health": True}},
    )
    assert not obs.is_healthy
    obs.services["checked_urls"]["http://localhost:8000/users"] = True
    assert obs.is_healthy
    obs.database["migration_applied"] = False
    assert not obs.is_healthy
