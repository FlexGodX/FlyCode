"""Tests for recovery reflex (plan §31)."""

import pytest

from flycode.cognition.recovery import FailureSignal, RecoveryState
from flycode.domain import BehaviorMode, Observation, ToolAction


class TestFailureSignal:
    """Tests for FailureSignal."""

    def test_no_failures(self) -> None:
        """A healthy state produces zero failures."""
        obs = Observation(
            task="Fix bug",
            git_state={"status": "clean"},
            tests={"status": "passing", "failed": 0},
            build={"status": "passed"},
            docker={"status": "running", "error": False},
            database={"status": "healthy", "connected": True},
            services={"status": "healthy", "app_running": True},
            recent_logs=[],
            recent_errors=[],
            resource_state={},
            action_history_summary={},
        )

        signal = FailureSignal.from_observation(obs)

        assert signal.regression_count == 0
        assert signal.new_failing_tests == 0
        assert signal.container_crashed is False
        assert signal.database_not_healthy is False
        assert signal.service_not_healthy is False
        assert signal.migration_failure is False
        assert signal.severity_score == 0.0

    def test_database_failure(self) -> None:
        """Database failure produces database_not_healthy=True."""
        obs = Observation(
            task="Connect to DB",
            git_state={"status": "clean"},
            tests={"status": "unknown"},
            build={"status": "failed"},
            docker={"status": "running", "error": False},
            database={"status": "unhealthy", "connected": False},
            services={"status": "unknown", "app_running": False},
            recent_logs=["ERROR: database connection refused"],
            recent_errors=["connection refused"],
            resource_state={},
            action_history_summary={},
        )

        signal = FailureSignal.from_observation(obs)

        assert signal.database_not_healthy is True
        assert signal.regression_count == 1
        assert signal.severity_score == 1.0

    def test_multiple_failures(self) -> None:
        """Multiple failures compound severity."""
        obs = Observation(
            task="Fix critical issue",
            git_state={"status": "dirty"},
            tests={"status": "failing", "failed": 8},
            build={"status": "failed"},
            docker={"status": "healthy", "error": False},
            database={"status": "unhealthy", "connected": False},
            services={"status": "failed", "app_running": False},
            recent_logs=[
                "ERROR: database connection refused",
                "ERROR: service health check failed",
            ],
            recent_errors=[
                "database connection refused",
                "service health check failed",
            ],
            resource_state={},
            action_history_summary={},
        )

        signal = FailureSignal.from_observation(obs)

        assert signal.database_not_healthy is True
        assert signal.service_not_healthy is True
        assert signal.new_failing_tests == 8
        assert signal.severity_score == 1.0  # Max severity for multiple failures


class TestRecoveryState:
    """Tests for RecoveryState."""

    def test_no_recovery_on_healthy(self) -> None:
        """Healthy trajectory should not trigger recovery."""
        from flycode.domain import BehaviorMode
        from flycode.cognition import HabituationModule

        habituation = HabituationModule()

        for mode in [
            "explore",
            "diagnose",
            "plan",
            "execute",
            "verify",
        ]:
            obs = Observation(
                task="Task X",
                git_state={"status": "clean"},
                tests={"status": "passing", "failed": 0},
                build={"status": "passed"},
                docker={"status": "running", "error": False},
                database={"status": "healthy", "connected": True},
                services={"status": "healthy", "app_running": True},
                recent_logs=[],
                recent_errors=[],
                resource_state={},
                action_history_summary={},
                current_behavior_mode=mode,
                observation_id="obs-0",
            )
            habituation.record(
                ToolAction(id="hab-1", tool="run", arguments={"text": "echo hello"}),
                mode,
                1.0,
                True,
            )

        state = RecoveryState()
        state.evaluate(obs)

        assert not state.should_trigger_recovery()
        assert state.get_recommended_mode() is None

    def test_recovery_on_critical_failure(self) -> None:
        """Critical failures should trigger RECOVER mode."""
        state = RecoveryState()

        # Healthy start
        state.evaluate(
            Observation(
                task="Fix critical issue",
                git_state={"status": "clean"},
                tests={"status": "passing", "failed": 0},
                build={"status": "passed"},
                docker={"status": "running", "error": False},
                database={"status": "healthy", "connected": True},
                services={"status": "healthy", "app_running": True},
                recent_logs=[],
                recent_errors=[],
                resource_state={},
                action_history_summary={},
                current_behavior_mode="execute",
                observation_id="obs-0",
            )
        )

        # Database crash - severity 1.0
        state.evaluate(
            Observation(
                task="Fix critical issue",
                git_state={"status": "dirty"},
                tests={"status": "failing", "failed": 10},
                build={"status": "failed"},
                docker={"status": "crashed", "error": "Container exited with code 1"},
                database={"status": "unhealthy", "connected": False},
                services={"status": "failed", "app_running": False},
                recent_logs=["ERROR: database container exited"],
                recent_errors=["database container exited"],
                resource_state={},
                action_history_summary={"total_calls": 5},
                current_behavior_mode="execute",
                observation_id="obs-1",
            )
        )

        assert state.should_trigger_recovery()
        assert state.get_recommended_mode() == BehaviorMode.RECOVER

    def test_diagnose_on_non_critical_failures(self) -> None:
        """Non-critical failures should suggest DIAGNOSE mode."""
        state = RecoveryState()

        # Some failures, but not critical
        state.evaluate(
            Observation(
                task="Fix bug",
                git_state={"status": "dirty"},
                tests={"status": "failing", "failed": 2},
                build={"status": "passed"},
                docker={"status": "running", "error": False},
                database={"status": "healthy", "connected": True},
                services={"status": "healthy", "app_running": True},
                recent_logs=["ERROR: lint warning"],
                recent_errors=["lint warning"],
                resource_state={},
                action_history_summary={"total_calls": 3},
                current_behavior_mode="plan",
                observation_id="obs-0",
            )
        )

        assert not state.should_trigger_recovery()
        assert state.should_trigger_diagnose()
        assert state.get_recommended_mode() == BehaviorMode.DIAGNOSE

    def test_empty_infra_sections_are_unknown_not_failed(self) -> None:
        """SoftwareGym leaves docker/database/services empty until the tools
        are called; empty sections must not trigger the reflex."""
        obs = Observation(task="Fix bug")

        signal = FailureSignal.from_observation(obs)

        assert signal.database_not_healthy is False
        assert signal.service_not_healthy is False
        assert signal.container_crashed is False
        assert signal.severity_score == 0.0

        state = RecoveryState()
        state.evaluate(obs)
        assert not state.should_trigger_recovery()
        assert state.get_recommended_mode() is None

    def test_healthy_observation_clears_recovery(self) -> None:
        """A fully healthy observation after a critical failure ends recovery."""
        state = RecoveryState()

        state.evaluate(
            Observation(
                task="Fix critical issue",
                tests={"status": "failing", "failed": 3},
                docker={"status": "crashed", "error": "Container exited with code 1"},
                database={"status": "unhealthy", "connected": False},
                services={"status": "failed", "app_running": False},
            )
        )
        assert state.should_trigger_recovery()

        state.evaluate(
            Observation(
                task="Fix critical issue",
                tests={"status": "passing", "failed": 0},
                docker={"status": "running", "error": False},
                database={"status": "healthy", "connected": True},
                services={"status": "healthy", "app_running": True},
            )
        )

        assert not state.should_trigger_recovery()
        assert state.get_recommended_mode() is None


    def test_migration_failure_triggers_recovery(self) -> None:
        """A failed `alembic upgrade` raises RECOVER on its own (plan §31).

        Regression guard: this signal used to be derived from the failing-test
        count, so any failing test looked like a migration failure and a real
        broken migration looked like nothing at all.
        """
        obs = Observation(
            task="Run migrations",
            tests={"status": "unknown"},
            database={"migration_failed": True},
            recent_errors=["alembic upgrade head: can't locate revision"],
        )

        signal = FailureSignal.from_observation(obs)

        assert signal.migration_failure is True
        assert signal.severity_score == 1.0

        state = RecoveryState()
        state.evaluate(obs)
        assert state.should_trigger_recovery()
        assert state.get_recommended_mode() == BehaviorMode.RECOVER

    def test_failing_tests_are_not_migration_failures(self) -> None:
        """Only an actual migration failure sets the flag."""
        obs = Observation(task="Fix failing tests", tests={"status": "failed", "failed": 3})

        signal = FailureSignal.from_observation(obs)

        assert signal.migration_failure is False
        assert signal.new_failing_tests == 3

    def test_container_crash_triggers_recovery(self) -> None:
        """A compose failure recorded by SoftwareGym raises RECOVER."""
        obs = Observation(
            task="Start the stack",
            tests={"status": "unknown"},
            docker={"running": False, "error": "docker compose up failed: port is already allocated"},
        )

        signal = FailureSignal.from_observation(obs)

        assert signal.container_crashed is True
        assert signal.severity_score == 1.0



class TestRecoveryRelease:
    """The reflex must release control when the critical failure never changes.

    E.g. no Docker daemon: `docker compose up` fails, `docker["error"]` stays
    set, and pinning every remaining step in RECOVER is useless - the agent
    cannot fix missing infrastructure.
    """

    def _broken_obs(self) -> Observation:
        return Observation(
            task="Start the stack",
            tests={"status": "unknown"},
            docker={"running": False, "error": "docker compose up failed"},
        )

    def test_unchanged_critical_signal_releases_recover(self) -> None:
        state = RecoveryState()
        obs = self._broken_obs()

        # First signal + MAX_UNCHANGED_CRITICAL-1 repeats: still RECOVER.
        for _ in range(RecoveryState.MAX_UNCHANGED_CRITICAL):
            state.evaluate(obs)
            assert state.get_recommended_mode() == BehaviorMode.RECOVER

        # The next identical signal means recovery changed nothing: release.
        state.evaluate(obs)
        assert state.get_recommended_mode() != BehaviorMode.RECOVER
        assert state.should_trigger_recovery()  # the failure itself is still real

    def test_changing_signal_keeps_recover(self) -> None:
        state = RecoveryState()
        obs = self._broken_obs()
        for _ in range(RecoveryState.MAX_UNCHANGED_CRITICAL + 2):
            state.evaluate(obs)
        # A *different* critical signal (new failing tests on top) resets the streak.
        state.evaluate(
            Observation(
                task="Start the stack",
                tests={"status": "failed", "failed": 3},
                docker={"running": False, "error": "docker compose up failed"},
            )
        )
        assert state.get_recommended_mode() == BehaviorMode.RECOVER

