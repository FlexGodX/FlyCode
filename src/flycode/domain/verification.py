"""Versioned evidence contracts for a trusted local pytest check (plan §101)."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

TrustMode = Literal['trusted-local-process', 'untrusted']
VerdictStatus = Literal['passed', 'failed', 'inconclusive', 'cancelled', 'invalidated']
CaseOutcome = Literal['passed', 'failed', 'error', 'skipped', 'xfail', 'xpass', 'timeout', 'cancelled', 'unknown']


class VerificationModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)


class SubjectSnapshot(VerificationModel):
    version: str = 'subject-v1'
    subject_hash: str
    files: dict[str, str]
    modes: dict[str, int]
    excluded: list[str]
    base: str | None
    inventory: list[str]
    environment: dict


class CheckSpec(VerificationModel):
    id: str = 'pytest'
    profile: Literal['pytest-v1'] = 'pytest-v1'
    required: bool = True
    timeout: float = Field(default=120., gt=0, le=300)
    oracle_version: str = 'pytest-observer+junit-v1'


class VerificationPlan(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    version: str = 'verification-plan-v1'
    subject_hash: str
    contract_hash: str
    checks: list[CheckSpec]
    trust: TrustMode = 'trusted-local-process'
    reruns: int = Field(default=0, ge=0, le=2)


class CaseResult(VerificationModel):
    id: str
    outcome: CaseOutcome
    duration: float = Field(default=0., ge=0)
    attempt: int = Field(default=1, ge=1, le=3)
    flaky: bool = False
    diagnostics: list[str] = Field(default_factory=list)


class CheckAttempt(VerificationModel):
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
    evaluator_version: str = 'pytest-verdict-v1'
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
