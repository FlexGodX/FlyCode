"""Build owner-approved requirement/check graphs bound to exact staged subjects."""

from collections.abc import Sequence
from pathlib import PurePosixPath

from ..domain.verification import (
    AcceptanceContract,
    CheckSpec,
    SubjectSnapshot,
    VerificationPlan,
)
from .snapshot import digest

STAGE_ORDER = {stage: index for index, stage in enumerate(('baseline', 'target', 'regression', 'integration'))}
STAGES = set(STAGE_ORDER)


def contract_hash(contract: AcceptanceContract) -> str:
    semantic = contract.model_dump(exclude={'title'})
    semantic['criteria'] = [criterion.model_dump(exclude={'title'}) for criterion in contract.criteria]
    return digest(semantic)


def validate_target(target: str) -> str:
    path = target.split('::', 1)[0]
    if (not path or path.startswith('-') or '\\' in target or any(ord(char) < 32 for char in target)
            or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
            or not path.endswith('.py')):
        raise ValueError('Targets must be relative Python test paths or node IDs')
    return target


def registered_checks(checks: list[CheckSpec], contract: AcceptanceContract) -> list[CheckSpec]:
    checks = [CheckSpec.model_validate(check.model_dump()) for check in checks]
    by_id = {check.id: check for check in checks}
    if len(by_id) != len(checks) or not all(by_id):
        raise ValueError('Check IDs must be unique and nonempty')
    if {check.stage for check in checks if check.required} != STAGES:
        raise ValueError('Acceptance requires baseline, target, regression and integration checks')
    for check in checks:
        if check.oracle_version != 'pytest-observer+junit-v1' or check.version != 'check-v1':
            raise ValueError('Unsupported oracle/check version')
        if check.expected_outcome == 'failed' and check.stage != 'baseline':
            raise ValueError('Expected failure is only a baseline negative control')
        for target in check.targets:
            validate_target(target)
        for dependency in check.dependencies:
            if dependency not in by_id or (check.required and not by_id[dependency].required):
                raise ValueError('Required dependency is absent or optional')
            if STAGE_ORDER[by_id[dependency].stage] > STAGE_ORDER[check.stage]:
                raise ValueError('A check cannot depend on a later stage')
    visited: set[str] = set()
    pending: set[str] = set()

    def visit(check_id: str):
        if check_id in pending:
            raise ValueError('Check dependency cycle')
        if check_id in visited:
            return
        pending.add(check_id)
        for dependency in by_id[check_id].dependencies:
            visit(dependency)
        pending.remove(check_id)
        visited.add(check_id)

    for check in checks:
        visit(check.id)
    for criterion in contract.criteria:
        if len(set(criterion.check_ids)) != len(criterion.check_ids):
            raise ValueError('Duplicate criterion check')
        if len(set(criterion.expected_case_ids)) != len(criterion.expected_case_ids):
            raise ValueError('Duplicate expected case')
        for case_id in criterion.expected_case_ids:
            if '::' not in validate_target(case_id):
                raise ValueError('Criteria require exact case IDs')
        if any(check_id not in by_id or (criterion.required and not by_id[check_id].required)
               for check_id in criterion.check_ids):
            raise ValueError(f'Criterion {criterion.id} lost a required check')
    return checks


class PlanBuilder:
    def build(self, snapshot: SubjectSnapshot, contract: AcceptanceContract, checks: list[CheckSpec], *,
              subjects: dict[str, SubjectSnapshot], approval_hash: str, proposals: Sequence[CheckSpec] = (),
              reruns: int = 0) -> VerificationPlan:
        contract = AcceptanceContract.model_validate(contract.model_dump())
        checks = registered_checks(checks, contract)
        if set(subjects) != STAGES or subjects['integration'].subject_hash != snapshot.subject_hash:
            raise ValueError('Exact staged subjects and integrated result are required')
        if snapshot.ref is None:
            raise ValueError('Acceptance requires server-owned subject references')
        scope = snapshot.ref.model_dump(exclude={'attempt_id'})
        for subject in subjects.values():
            if subject.ref is None or subject.ref.model_dump(exclude={'attempt_id'}) != scope:
                raise ValueError('Foreign subject scope')
        ids = {check.id for check in checks}
        for proposal in proposals:
            if proposal.required or proposal.id in ids:
                raise ValueError('Agent proposals may only append optional checks')
            checks.append(CheckSpec.model_validate(proposal.model_dump()))
            ids.add(proposal.id)
        checks = registered_checks(checks, contract)
        bound = []
        for check in checks:
            subject_hash = subjects[check.stage].subject_hash
            if check.subject_hash is not None and check.subject_hash != subject_hash:
                raise ValueError('Check bound to another snapshot')
            bound.append(check.model_copy(update={'subject_hash': subject_hash}))
        return VerificationPlan(subject_hash=snapshot.subject_hash, contract_hash=contract_hash(contract), checks=bound,
            acceptance=contract, scope='acceptance-contract', approval_hash=approval_hash,
            subjects={stage: SubjectSnapshot.model_validate(subject.model_dump()) for stage, subject in subjects.items()},
            reruns=reruns)
