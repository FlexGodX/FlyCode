"""Server-owned immutable verification catalogue (plan §101, TST-13)."""
from typing import Literal
from uuid import uuid4

from pydantic import Field

from .source import SourceRoot
from .verification import (
    AcceptanceContract,
    CheckSpec,
    SubjectSnapshot,
    TrustMode,
    VerificationModel,
)


class VerificationProfileDraft(VerificationModel):
    title: str = Field(min_length=1, max_length=200)
    checks: list[CheckSpec] = Field(default_factory=lambda: [CheckSpec()], min_length=1, max_length=64)
    trust: TrustMode = 'trusted-local-process'
    reruns: int = Field(default=0, ge=0, le=2)
    acceptance: AcceptanceContract | None = None


class RegisteredSubject(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_revision: int = Field(ge=1)
    source_root: SourceRoot
    root: SourceRoot
    snapshot: SubjectSnapshot
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float


class RegisteredVerificationProfile(VerificationModel):
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[0-9a-f]{32}$')
    version: Literal[1] = 1
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    project_revision: int = Field(ge=1)
    draft: VerificationProfileDraft
    approval_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float
