from __future__ import annotations

import asyncio
from collections import defaultdict

from echopulsing.services.models import ChatState, Track


class QueueManager:
    def __init__(self) -> None:
        self._states: dict[int, ChatState] = {}
        self._locks = defaultdict(asyncio.Lock)

    def _state(self, chat_id: int) -> ChatState:
        if chat_id not in self._states:
            self._states[chat_id] = ChatState()
        return self._states[chat_id]

    async def enqueue(self, chat_id: int, track: Track) -> int:
        async with self._locks[chat_id]:
            state = self._state(chat_id)
            state.queue.append(track)
            return len(state.queue)

    async def enqueue_front(self, chat_id: int, track: Track) -> int:
        async with self._locks[chat_id]:
            state = self._state(chat_id)
            state.queue.insert(0, track)
            return len(state.queue)

    async def get_current(self, chat_id: int) -> Track | None:
        return self._state(chat_id).current

    async def set_current(self, chat_id: int, track: Track | None) -> None:
        async with self._locks[chat_id]:
            self._state(chat_id).current = track

    async def pop_next(self, chat_id: int) -> Track | None:
        async with self._locks[chat_id]:
            state = self._state(chat_id)
            if not state.queue:
                return None
            return state.queue.pop(0)

    async def list_queue(self, chat_id: int) -> list[Track]:
        return list(self._state(chat_id).queue)

    async def clear(self, chat_id: int) -> list[Track]:
        async with self._locks[chat_id]:
            state = self._state(chat_id)
            tracks = list(state.queue)
            state.queue.clear()
            return tracks

    async def set_repeat(self, chat_id: int, enabled: bool) -> None:
        async with self._locks[chat_id]:
            self._state(chat_id).repeat = enabled

    async def get_repeat(self, chat_id: int) -> bool:
        return self._state(chat_id).repeat

    async def set_volume(self, chat_id: int, value: int) -> int:
        value = max(10, min(value, 200))
        async with self._locks[chat_id]:
            self._state(chat_id).volume = value
            return value

    async def get_volume(self, chat_id: int) -> int:
        return self._state(chat_id).volume
