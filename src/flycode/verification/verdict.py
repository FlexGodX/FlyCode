"""Deterministic evaluator: process exit and model statements cannot grant a pass."""

from collections.abc import Sequence

from pydantic import Field

from ..domain.verification import (
    AcceptanceContract,
    CaseResult,
    CheckAttempt,
    CheckSpec,
    VerdictStatus,
    VerificationModel,
)


def _evaluate_suite(attempts: list[CheckAttempt], invalidated: bool) -> tuple[VerdictStatus, list[str], list[CaseResult]]:
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


class ContractEvaluation(VerificationModel):
    status: VerdictStatus
    reasons: list[str]
    cases: list[CaseResult]
    criteria: dict[str, VerdictStatus]
    checks: dict[str, VerdictStatus]
    coverage: float = Field(ge=0, le=1)


def evaluate_contract(attempts: list[CheckAttempt], invalidated: bool, contract: AcceptanceContract,
                      checks: list[CheckSpec]) -> ContractEvaluation:
    statuses: dict[str, VerdictStatus] = {}
    criteria: dict[str, VerdictStatus] = {}
    reasons: list[str] = []
    cases: list[CaseResult] = []
    by_id = {check.id: check for check in checks}
    for check in checks:
        observed = [attempt for attempt in attempts if attempt.check_id == check.id]
        status, detail, check_cases = _evaluate_suite(observed, False)
        cases.extend(check_cases)
        if check.expected_outcome == 'failed':
            confirmed = (observed and all(attempt.complete and attempt.exit_code == 1 and attempt.cases
                and all(case.outcome == 'failed' for case in attempt.cases) for attempt in observed)
                and all(set(attempt.collected) == set(observed[0].collected) for attempt in observed))
            status = 'passed' if confirmed else 'inconclusive'
            detail = ['Baseline negative control confirmed' if confirmed else 'Negative control sensitivity not confirmed']
        statuses[check.id] = status
        reasons.extend(f'{check.id}: {reason}' for reason in detail)
    for criterion in contract.criteria:
        linked = [statuses.get(check_id, 'inconclusive') for check_id in criterion.check_ids]
        status = 'failed' if 'failed' in linked else 'inconclusive' if any(item != 'passed' for item in linked) else 'passed'
        for check_id in criterion.check_ids:
            observed = [attempt for attempt in attempts if attempt.check_id == check_id]
            expected = by_id[check_id].expected_outcome if check_id in by_id else 'passed'
            if not observed or any(not set(criterion.expected_case_ids).issubset(attempt.collected) or
                any(not any(case.id == case_id and case.outcome == expected for case in attempt.cases)
                    for case_id in criterion.expected_case_ids) for attempt in observed):
                if status != 'failed':
                    status = 'inconclusive'
                reasons.append(f'{criterion.id}: missing or nonmatching required cases in {check_id}')
        criteria[criterion.id] = status
    mandatory = [statuses[check.id] for check in checks if check.required]
    mandatory.extend(criteria[criterion.id] for criterion in contract.criteria if criterion.required)
    if invalidated:
        status = 'invalidated'
        reasons.append('Runner modified subject input bytes')
    elif any(attempt.cancelled for attempt in attempts):
        status = 'cancelled'
    elif 'failed' in mandatory:
        status = 'failed'
    elif any(attempt.error_class == 'cleanup' for attempt in attempts):
        status = 'inconclusive'
        reasons.append('Owned process cleanup is unconfirmed, including optional checks')
    elif not mandatory or any(item != 'passed' for item in mandatory):
        status = 'inconclusive'
    else:
        status = 'passed'
    required = [criterion for criterion in contract.criteria if criterion.required]
    coverage = sum(criteria[criterion.id] == 'passed' for criterion in required) / len(required)
    return ContractEvaluation(status=status, reasons=reasons, cases=cases, criteria=criteria,
                              checks=statuses, coverage=coverage)


def evaluate(attempts: list[CheckAttempt], invalidated: bool, *, contract: AcceptanceContract | None = None,
             checks: Sequence[CheckSpec] = ()) -> tuple[VerdictStatus, list[str], list[CaseResult]]:
    if contract is None:
        return _evaluate_suite(attempts, invalidated)
    result = evaluate_contract(attempts, invalidated, contract, list(checks))
    return result.status, result.reasons, result.cases
