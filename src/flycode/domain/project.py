"""Host-owned physical project identity, independent of local UI selectors."""
from typing import Literal

from pydantic import Field, model_validator

from .orchestration import QueueModel
from .source import SourceRoot


class CanonicalProject(QueueModel):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    root: SourceRoot
    revision: int = Field(default=1, ge=1)
    state: Literal['active', 'revoked'] = 'active'
    created_by: str = Field(min_length=1, max_length=200)
    created_at: float
    revoked_at: float | None = None

    @model_validator(mode='after')
    def revocation(self):
        if (self.state == 'revoked') != (self.revoked_at is not None):
            raise ValueError('Revocation state requires its timestamp')
        return self
