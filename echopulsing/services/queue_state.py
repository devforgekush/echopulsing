from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from echopulsing.services.models import Track


@dataclass(slots=True)
class AsyncTrackQueue:
    _items: deque[Track] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def put(self, track: Track) -> int:
        async with self._lock:
            self._items.append(track)
            return len(self._items)

    async def put_front(self, track: Track) -> int:
        async with self._lock:
            self._items.appendleft(track)
            return len(self._items)

    async def get_nowait(self) -> Track | None:
        async with self._lock:
            if not self._items:
                return None
            return self._items.popleft()

    async def peek_nowait(self) -> Track | None:
        async with self._lock:
            if not self._items:
                return None
            return self._items[0]

    async def snapshot(self) -> list[Track]:
        async with self._lock:
            return list(self._items)

    async def clear(self) -> list[Track]:
        async with self._lock:
            tracks = list(self._items)
            self._items.clear()
            return tracks

    async def remove_first(self, predicate: Callable[[Track], bool]) -> Track | None:
        async with self._lock:
            for index, track in enumerate(self._items):
                if not predicate(track):
                    continue
                items = list(self._items)
                removed = items.pop(index)
                self._items = deque(items)
                return removed
            return None

    async def size(self) -> int:
        async with self._lock:
            return len(self._items)

    async def shuffle(self) -> int:
        async with self._lock:
            if len(self._items) < 2:
                return len(self._items)
            items = list(self._items)
            random.shuffle(items)
            self._items = deque(items)
            return len(self._items)


@dataclass(slots=True)
class ChatState:
    current: Track | None = None
    queue: AsyncTrackQueue = field(default_factory=AsyncTrackQueue)
    loop_mode: str = "off"
    volume: int = 100