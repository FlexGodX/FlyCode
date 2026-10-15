"""Deterministic evaluator: process exit and model statements cannot grant a pass."""

from ..domain.verification import CaseResult, CheckAttempt, VerdictStatus


def evaluate(attempts: list[CheckAttempt], invalidated: bool) -> tuple[VerdictStatus, list[str], list[CaseResult]]:
    cases = [case for attempt in attempts for case in attempt.cases]
    reasons = [reason for attempt in attempts for reason in attempt.reasons]
    outcomes: dict[str, set[str]] = {}
    for case in cases:
        outcomes.setdefault(case.id, set()).add(case.outcome)
    flaky = {key for key, values in outcomes.items() if 'passed' in values and 'failed' in values}
    cases = [case.model_copy(update={'flaky': case.id in flaky}) for case in cases]
    if invalidated:
        return 'invalidated', [*reasons, 'Runner modified subject input bytes'], cases
    if any(attempt.cancelled for attempt in attempts):
        return 'cancelled', reasons, cases
    if any(case.outcome == 'failed' and case.id not in flaky for case in cases):
        return 'failed', [*reasons, 'Mandatory assertions failed'], cases
    if flaky:
        return 'inconclusive', [*reasons, 'Mandatory cases are flaky'], cases
    if attempts and any(set(attempt.collected) != set(attempts[0].collected) for attempt in attempts[1:]):
        return 'inconclusive', [*reasons, 'Test collection changed across attempts'], cases
    if (not attempts or not cases or any(not attempt.complete or attempt.exit_code != 0 for attempt in attempts)
            or any(case.outcome != 'passed' for case in cases)):
        return 'inconclusive', [*reasons, 'Required pytest evidence is empty, incomplete or not passing'], cases
    return 'passed', ['Registered pytest check passed on the captured subject'], cases
