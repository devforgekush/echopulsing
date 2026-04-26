from __future__ import annotations

from echopulsing.services.models import Track


class DisabledVoiceService:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def _error(self) -> RuntimeError:
        return RuntimeError(self.reason)

    async def play_next(self, chat_id: int) -> Track | None:
        raise self._error()

    async def enqueue_or_play(self, chat_id: int, track: Track) -> tuple[str, int]:
        raise self._error()

    async def pause(self, chat_id: int) -> None:
        raise self._error()

    async def resume(self, chat_id: int) -> None:
        raise self._error()

    async def skip(self, chat_id: int) -> Track | None:
        raise self._error()

    async def stop(self, chat_id: int) -> None:
        raise self._error()

    async def set_volume(self, chat_id: int, volume: int) -> int:
        raise self._error()

    async def get_elapsed(self, chat_id: int) -> int:
        return 0
