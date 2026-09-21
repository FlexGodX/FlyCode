"""Curiosity / novelty tracking (plan §22).

Tracks whether the current observation contains anything the agent has not
seen before: new files, new stack traces, new services, new symbols. When
progress and novelty are both low, upstream policy should raise EXPLORE bias.
"""

import hashlib

from pydantic import BaseModel, Field

from ..domain import Observation


class NoveltyState(BaseModel):
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    new_signatures: list[str] = Field(default_factory=list)
    seen_count: int = 0


class NoveltyModule:
    """Set-membership novelty detector over coarse observation signatures."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._seen: set[str] = set()
        self._state = NoveltyState()

    def _signatures(self, observation: Observation) -> list[str]:
        sigs: list[str] = []
        for f in observation.file_tree:
            sigs.append(f"file:{f}")
        for f in observation.created_files:
            sigs.append(f"created:{f}")
        for err in observation.recent_errors:
            sigs.append(f"error:{_fingerprint(err)}")
        for svc, state in observation.services.items():
            sigs.append(f"service:{svc}:{state}")
        if observation.database:
            sigs.append(f"db:{observation.database.get('name', 'default')}")
        return sigs

    def evaluate(self, observation: Observation) -> NoveltyState:
        sigs = self._signatures(observation)
        new = [s for s in sigs if s not in self._seen]
        self._seen.update(sigs)

        novelty = min(1.0, len(new) / 5.0) if sigs else 0.0
        self._state = NoveltyState(novelty=novelty, new_signatures=new, seen_count=len(self._seen))
        return self._state

    def get_state(self) -> NoveltyState:
        return self._state


def _fingerprint(text: str) -> str:
    # Collapse volatile bits (line numbers, addresses) so repeated occurrences
    # of "the same" error are not counted as novel every time.
    normalized = "".join(c for c in text if not c.isdigit())
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]
