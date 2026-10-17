"""Versioned evidence contracts for a trusted local pytest check (plan §101)."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

TrustMode = Literal['trusted-local-process', 'untrusted']
VerdictStatus = Literal['passed', 'failed', 'inconclusive', 'cancelled', 'invalidated']
CaseOutcome = Literal['passed', 'failed', 'error', 'skipped', 'xfail', 'xpass', 'timeout', 'cancelled', 'unknown']
AttemptError = Literal['assertion', 'collection', 'setup', 'timeout', 'cancelled', 'report', 'unavailable', 'policy', 'cleanup']


class VerificationModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)


class SubjectRef(VerificationModel):
    project_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)


class AcceptanceCriterion(VerificationModel):
    id: str = Field(min_length=1)
    title: str = ''
    required: bool = True
    check_ids: list[str] = Field(min_length=1)
    expected_case_ids: list[str] = Field(min_length=1)


class AcceptanceContract(VerificationModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str = ''
    criteria: list[AcceptanceCriterion] = Field(min_length=1)

    @model_validator(mode='after')
    def requirements(self):
        if len({item.id for item in self.criteria}) != len(self.criteria) or not any(c.required for c in self.criteria):
            raise ValueError('Acceptance needs unique criteria and at least one mandatory criterion')
        return self


class SubjectSnapshot(VerificationModel):
    version: str = 'subject-v1'
    subject_hash: str
    files: dict[str, str]
    modes: dict[str, int]
    excluded: list[str]
    base: str | None
    inventory: list[str]
    environment: dict
    ref: SubjectRef | None = None


class CheckSpec(VerificationModel):
    id: str = 'pytest'
    profile: Literal['pytest-v1'] = 'pytest-v1'
    required: bool = True
    timeout: float = Field(default=120., gt=0, le=300)
    oracle_version: str = 'pytest-observer+junit-v1'
    version: str = 'check-v1'
    stage: Literal['baseline', 'target', 'regression', 'integration'] = 'target'
    targets: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    expected_outcome: Literal['passed', 'failed'] = 'passed'
    subject_hash: str | None = None


class VerificationPlan(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    version: str = 'verification-plan-v1'
    subject_hash: str
    contract_hash: str
    checks: list[CheckSpec]
    trust: TrustMode = 'trusted-local-process'
    reruns: int = Field(default=0, ge=0, le=2)
    scope: Literal['registered-suite', 'acceptance-contract'] = 'registered-suite'
    acceptance: AcceptanceContract | None = None
    approval_hash: str | None = None
    subjects: dict[str, SubjectSnapshot] = Field(default_factory=dict)


class CaseResult(VerificationModel):
    id: str
    outcome: CaseOutcome
    duration: float = Field(default=0., ge=0)
    attempt: int = Field(default=1, ge=1, le=3)
    flaky: bool = False
    diagnostics: list[str] = Field(default_factory=list)
    check_id: str | None = None
    subject_hash: str | None = None


class ProcessRef(VerificationModel):
    version: Literal['pytest-custody-v1', 'tool-custody-v1', 'worker-custody-v1'] = 'pytest-custody-v1'
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    directory: str
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ProcessReceipt(VerificationModel):
    ref: ProcessRef
    state: Literal['preparing', 'admitted', 'stopped', 'uncertain'] = 'preparing'
    started: bool = False
    stop_confirmed: bool = False
    exit_code: int | None = None
    reason: str | None = None
    pid: int | None = Field(default=None, gt=0)


class CheckAttempt(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    check_id: str = 'pytest'
    plan_id: str | None = None
    subject_hash: str | None = None
    state: Literal['finished', 'blocked', 'cancelled', 'interrupted'] = 'finished'
    error_class: AttemptError | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    process: ProcessRef | None = None
    process_started: bool = False
    stop_confirmed: bool = False
    process_reason: str | None = None
    number: int
    argv: list[str]
    exit_code: int | None
    duration: float
    cases: list[CaseResult] = Field(default_factory=list)
    collected: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    cancelled: bool = False
    complete: bool = False


class EvidenceManifest(VerificationModel):
    version: str = 'verification-evidence-v1'
    evidence_id: str
    subject_hash: str
    plan_hash: str
    trust: str
    environment: dict
    artifacts: dict[str, str]
    invocations: list[dict]
    complete: bool
    redacted: bool
    limitations: list[str]


class VerificationVerdict(VerificationModel):
    version: str = 'verification-verdict-v1'
    subject_hash: str
    plan_hash: str
    environment: dict
    status: VerdictStatus
    reasons: list[str]
    cases: list[CaseResult]
    evidence_id: str
    evidence_hash: str
    required_coverage: float = Field(ge=0, le=1)
    scope: Literal['registered-suite', 'acceptance-contract'] = 'registered-suite'
    contract_hash: str | None = None
    criteria: dict[str, VerdictStatus] = Field(default_factory=dict)
    checks: dict[str, VerdictStatus] = Field(default_factory=dict)
    evaluator_version: str = 'pytest-verdict-v1'
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
