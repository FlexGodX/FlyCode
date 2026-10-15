"""Recovery Reflex - automatic behavioral pivoting on system failures (plan §31).

Detects critical failures (regressions, crashes, migrations, DB failures,
service failures, unexpected test increases) and automatically raises
RECOVER or DIAGNOSE behavior mode without LLM intervention.

This is a fast, automatic reflex — bypassing LLM to prevent the agent from
continuing down a clearly broken trajectory.
"""

from pydantic import BaseModel, Field
from typing_extensions import Annotated, Literal

from ..domain import BehaviorMode, Observation


class FailureSignal(BaseModel):
    """Critical failure types that trigger recovery reflex (plan §31)."""

    regression_count: Annotated[int, Field(ge=0)] = 0
    new_failing_tests: Annotated[int, Field(ge=0)] = 0
    container_crashed: bool = False
    database_not_healthy: bool = False
    service_not_healthy: bool = False
    migration_failure: bool = False
    unknown_trace: bool = False

    # Aggregate score (0.0 = healthy, 1.0 = critical failure)
    severity_score: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0

    @classmethod
    def from_observation(cls, obs: Observation) -> "FailureSignal":
        """Extract failure signals from an Observation.

        Missing infra sections (empty dicts) mean "not instrumented yet",
        not "failed" - the SoftwareGym only populates docker/database/services
        after the corresponding tool has been called.
        """
        # Infra-level failures (critical)
        db = obs.database
        db_status = db.get("status")
        database_not_healthy = bool(db) and (
            (db_status is not None and db_status != "healthy")
            or db.get("connected") is False
        )
        service_not_healthy = bool(obs.services) and obs.services.get("app_running") is False
        container_crashed = bool(obs.docker.get("error"))

        # Migration failure is its own signal (plan §31): a failed
        # `alembic upgrade` means the schema is not what the app expects, which
        # is not the same thing as a failing test.
        migration_failure = bool(db.get("migration_failed"))

        # Test regressions
        test_failures = obs.failing_tests

        # Any critical infra failure is immediately maximal severity; otherwise
        # severity tracks the number of failing tests.
        critical_infra = (
            database_not_healthy or service_not_healthy or container_crashed or migration_failure
        )
        severity_score = 1.0 if critical_infra else min(1.0, test_failures / 5.0)

        return cls(
            regression_count=len(obs.recent_errors),
            new_failing_tests=test_failures,
            container_crashed=container_crashed,
            database_not_healthy=database_not_healthy,
            service_not_healthy=service_not_healthy,
            migration_failure=migration_failure,
            unknown_trace=bool(obs.recent_errors and not obs.recent_logs),
            severity_score=severity_score,
        )


class RecoveryState:
    """Recovery tracking across the agent trajectory."""

    #: Consecutive identical critical signals after which the reflex releases
    #: control: the failure is not actionable by the agent (e.g. no Docker
    #: daemon), so pinning every remaining step in RECOVER is useless.
    MAX_UNCHANGED_CRITICAL = 3

    def __init__(self) -> None:
        self._failures: list[FailureSignal] = []
        self._recovery_attempts: dict[str, int] = {}
        self._needs_recovery: bool = False
        self._override_mode: BehaviorMode | None = None
        self._unchanged_critical: int = 0

    def evaluate(self, observation: Observation) -> "RecoveryState":
        """Evaluate current observation and decide if recovery is needed."""
        signal = FailureSignal.from_observation(observation)

        # A fully healthy observation clears the reflex: recovery succeeded.
        if signal.severity_score == 0.0 and self._needs_recovery:
            self.clear()

        # Count consecutive *unchanged* critical signals so the reflex can
        # release control when the failure is outside the agent's reach.
        if (
            self._failures
            and signal.severity_score >= 0.7
            and signal == self._failures[-1]
        ):
            self._unchanged_critical += 1
        else:
            self._unchanged_critical = 0

        self._failures.append(signal)

        # If we hit a critical failure (severity >= 0.7), enter recovery
        critical_failures = sum(1 for f in self._failures if f.severity_score >= 0.7)
        if critical_failures > 0:
            self._needs_recovery = True

        return self

    def should_trigger_recovery(self) -> bool:
        """Check if we should trigger recovery reflex."""
        return self._needs_recovery

    def should_trigger_diagnose(self) -> bool:
        """Suggest DIAGNOSE if we have failures but no critical state."""
        has_failures = len(self._failures) > 0
        recent_failures = self._failures[-5:] if len(self._failures) > 5 else self._failures

        avg_severity = (
            sum(f.severity_score for f in recent_failures) / len(recent_failures)
        )

        return has_failures and avg_severity > 0.3

    def get_recommended_mode(self) -> BehaviorMode | None:
        """Get recommended behavior mode based on recovery state."""
        if self._override_mode is not None:
            return self._override_mode

        if self._needs_recovery and self._unchanged_critical < self.MAX_UNCHANGED_CRITICAL:
            return BehaviorMode.RECOVER

        if self.should_trigger_diagnose():
            return BehaviorMode.DIAGNOSE

        return None

    def clear(self) -> None:
        """Clear recovery state after successful recovery."""
        self._failures = []
        self._needs_recovery = False
        self._override_mode = None
        self._unchanged_critical = 0

    def log(self) -> str:
        """Return a human-readable summary."""
        lines = [
            f"Total failures observed: {len(self._failures)}",
            f"Critical failures (severity >= 0.7): {sum(1 for f in self._failures if f.severity_score >= 0.7)}",
            f"Recovery attempts: {self._recovery_attempts}",
            f"Currently in recovery: {self._needs_recovery}",
        ]
        return "\n".join(lines)


def evaluate_recovery(observation: Observation) -> RecoveryState:
    """Evaluate observation for recovery reflex."""
    state = RecoveryState()
    state.evaluate(observation)

    # If recovery is triggered and we don't already have a recovery mode,
    # override the current behavior mode
    recommended = state.get_recommended_mode()
    if recommended is not None:
        print(f"[RECOVERY] Triggered recovery reflex. Current: {observation.behavior_mode}, Recommended: {recommended}")
        # In production, this would actually change the behavior mode
        # For now, we just return the signal

    return state
