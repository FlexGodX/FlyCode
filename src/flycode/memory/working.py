"""Working memory - a small bounded fact list (plan §32).

Not a vector store: a rolling list of short, human-readable facts the LLM
cortex is shown verbatim on every turn (file contents just read, test output
just observed, a fix that was just applied). Old facts are evicted FIFO once
the cap is reached.
"""

from pydantic import BaseModel, Field

DEFAULT_CAPACITY = 30


class WorkingMemory(BaseModel):
    """Bounded FIFO list of significant facts (plan §32)."""

    facts: list[str] = Field(default_factory=list)
    capacity: int = DEFAULT_CAPACITY

    def add(self, fact: str) -> None:
        self.facts.append(fact)
        if len(self.facts) > self.capacity:
            self.facts = self.facts[-self.capacity :]

    def extend(self, facts: list[str]) -> None:
        for fact in facts:
            self.add(fact)

    def clear(self) -> None:
        self.facts = []

    def as_list(self) -> list[str]:
        return list(self.facts)
