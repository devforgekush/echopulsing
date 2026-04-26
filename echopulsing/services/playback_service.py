from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from echopulsing.services.models import Track


@dataclass(slots=True)
class PlayResult:
    track: Track
    state: str
    position: int
    resolve_ms: int
    total_ms: int


class PlaybackService:
    def __init__(self, voice: Any, ytdlp: Any, logger: Any) -> None:
        self._voice = voice
        self._ytdlp = ytdlp
        self._logger = logger

    async def play_query(
        self,
        *,
        chat_id: int,
        query: str,
        requester_id: int,
        requester_name: str,
    ) -> PlayResult:
        started_at = time.perf_counter()

        resolve_task = asyncio.create_task(
            self._ytdlp.resolve(query, requester_id, requester_name)
        )
        prewarm_task = asyncio.create_task(self._voice.prewarm_connection(chat_id))

        try:
            track = await resolve_task
        except Exception:
            if not prewarm_task.done():
                prewarm_task.cancel()
            try:
                await prewarm_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            raise
        resolve_ms = int((time.perf_counter() - started_at) * 1000)

        prewarm_error: Exception | None = None
        try:
            await prewarm_task
        except Exception as exc:  # pragma: no cover
            prewarm_error = exc

        state, position = await self._voice.enqueue_or_play(chat_id, track)
        total_ms = int((time.perf_counter() - started_at) * 1000)

        if prewarm_error:
            self._logger.warning("Prewarm failed in chat %s: %s", chat_id, prewarm_error)

        self._logger.info(
            "play_query chat=%s state=%s resolve_ms=%s total_ms=%s",
            chat_id,
            state,
            resolve_ms,
            total_ms,
        )
        return PlayResult(
            track=track,
            state=state,
            position=position,
            resolve_ms=resolve_ms,
            total_ms=total_ms,
        )
