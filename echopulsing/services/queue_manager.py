from __future__ import annotations

import asyncio
from collections import defaultdict

from echopulsing.services.models import Track
from echopulsing.services.queue_state import AsyncTrackQueue, ChatState


class QueueManager:
    _LOOP_MODES = ("off", "single", "all")

    def __init__(self) -> None:
        self._states: dict[int, ChatState] = {}
        self._locks = defaultdict(asyncio.Lock)

    def _state(self, chat_id: int) -> ChatState:
        if chat_id not in self._states:
            self._states[chat_id] = ChatState(queue=AsyncTrackQueue())
        return self._states[chat_id]

    async def enqueue(self, chat_id: int, track: Track) -> int:
        state = self._state(chat_id)
        return await state.queue.put(track)

    async def enqueue_front(self, chat_id: int, track: Track) -> int:
        state = self._state(chat_id)
        return await state.queue.put_front(track)

    async def get_current(self, chat_id: int) -> Track | None:
        async with self._locks[chat_id]:
            return self._state(chat_id).current

    async def set_current(self, chat_id: int, track: Track | None) -> None:
        async with self._locks[chat_id]:
            self._state(chat_id).current = track

    async def pop_next(self, chat_id: int) -> Track | None:
        state = self._state(chat_id)
        return await state.queue.get_nowait()

    async def peek_next(self, chat_id: int) -> Track | None:
        state = self._state(chat_id)
        return await state.queue.peek_nowait()

    async def list_queue(self, chat_id: int) -> list[Track]:
        state = self._state(chat_id)
        return await state.queue.snapshot()

    async def clear(self, chat_id: int) -> list[Track]:
        state = self._state(chat_id)
        return await state.queue.clear()

    async def shuffle(self, chat_id: int) -> int:
        state = self._state(chat_id)
        return await state.queue.shuffle()

    async def set_loop_mode(self, chat_id: int, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in self._LOOP_MODES:
            normalized = "off"
        async with self._locks[chat_id]:
            self._state(chat_id).loop_mode = normalized
            return normalized

    async def get_loop_mode(self, chat_id: int) -> str:
        async with self._locks[chat_id]:
            return self._state(chat_id).loop_mode

    async def cycle_loop_mode(self, chat_id: int) -> str:
        async with self._locks[chat_id]:
            state = self._state(chat_id)
            current = state.loop_mode if state.loop_mode in self._LOOP_MODES else "off"
            index = self._LOOP_MODES.index(current)
            next_mode = self._LOOP_MODES[(index + 1) % len(self._LOOP_MODES)]
            state.loop_mode = next_mode
            return next_mode

    async def set_repeat(self, chat_id: int, enabled: bool) -> None:
        await self.set_loop_mode(chat_id, "single" if enabled else "off")

    async def get_repeat(self, chat_id: int) -> bool:
        return (await self.get_loop_mode(chat_id)) == "single"

    async def set_volume(self, chat_id: int, value: int) -> int:
        value = max(10, min(value, 200))
        async with self._locks[chat_id]:
            self._state(chat_id).volume = value
            return value

    async def get_volume(self, chat_id: int) -> int:
        async with self._locks[chat_id]:
            return self._state(chat_id).volume
