"""Optional live progress channel shared by the CLI loop and local UI."""

from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel, Field

EventSink = Callable[[str, dict], None]


class AgentEvent(BaseModel):
    id: int
    kind: str
    data: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def emit(sink: EventSink | None, kind: str, **data) -> None:
    if sink is not None:
        sink(kind, data)
