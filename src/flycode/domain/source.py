"""Host resource ownership; legacy runs do not become canonical tasks."""
from typing import Literal

from pydantic import Field

from .orchestration import QueueModel
from .verification import ProcessRef


class SourceRoot(QueueModel):
    path: str
    identity: tuple[int, int]
    ancestors: tuple[tuple[int, int], ...]


class SourceClaim(QueueModel):
    id: str
    root: SourceRoot
    owner_kind: Literal['legacy', 'integration']
    owner_id: str
    coordinator_path: str
    coordinator_identity: tuple[int, int]
    attempt_id: str | None = None
    fence: int | None = None
    processes: tuple[ProcessRef, ...] = ()
    external_effects: bool = False
    revision: int = Field(default=1, ge=1)
    state: Literal['held', 'uncertain', 'released'] = 'held'
    reason: str | None = None
