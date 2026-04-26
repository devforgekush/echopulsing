from __future__ import annotations

from dataclasses import dataclass, field
from time import time

from echopulsing.services.queue_state import ChatState


@dataclass(slots=True)
class Track:
    title: str
    source_url: str
    webpage_url: str
    duration: int | None
    requester_id: int
    requester_name: str
    thumbnail: str | None
    stream_url: str | None = None
    id: str | None = None
    created_at: float = field(default_factory=time)
