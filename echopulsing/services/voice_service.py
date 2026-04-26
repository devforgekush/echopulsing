from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from pytgcalls import PyTgCalls
from pytgcalls import filters
from pytgcalls.types import MediaStream

from echopulsing.services.models import Track
from echopulsing.services.queue_manager import QueueManager
from echopulsing.services.ytdlp_service import YtDlpService


class VoiceService:
    def __init__(
        self,
        calls: PyTgCalls,
        queue: QueueManager,
        ytdlp: YtDlpService,
        logger: Any,
    ) -> None:
        self.calls = calls
        self.queue = queue
        self.ytdlp = ytdlp
        self.logger = logger
        self._lock = asyncio.Lock()
        self._started_at: dict[int, float] = {}
        self._paused_at: dict[int, float] = {}
        self._paused_total: dict[int, float] = {}
        self._active_chats: set[int] = set()
        self._auto_transition_callback: Callable[[int, Track | None], Awaitable[None]] | None = None
        self._register_stream_end_handler()

    def set_auto_transition_callback(
        self,
        callback: Callable[[int, Track | None], Awaitable[None]],
    ) -> None:
        self._auto_transition_callback = callback

    async def _notify_auto_transition(self, chat_id: int, track: Track | None) -> None:
        if self._auto_transition_callback is None:
            return
        try:
            await self._auto_transition_callback(chat_id, track)
        except Exception as exc:
            self.logger.warning("Auto transition UI callback failed in chat %s: %s", chat_id, exc)

    async def _safe_cleanup(self, track: Track | None) -> None:
        if not track:
            return
        for _ in range(5):
            try:
                await self.ytdlp.cleanup_track_file(track)
                return
            except Exception:
                await asyncio.sleep(0.4)

    async def _group_call_is_active(self, chat_id: int) -> bool:
        try:
            active_group_calls = await self.calls.group_calls
        except Exception:
            return False
        return chat_id in active_group_calls

    async def _is_connected(self, chat_id: int) -> bool:
        return await self._group_call_is_active(chat_id)

    async def _reset_state(self, chat_id: int, reason: str, keep_queue: bool = True) -> None:
        self._active_chats.discard(chat_id)
        self._started_at.pop(chat_id, None)
        self._paused_at.pop(chat_id, None)
        self._paused_total.pop(chat_id, None)
        await self.queue.set_current(chat_id, None)
        if not keep_queue:
            await self.queue.clear(chat_id)
        self.logger.info("VC disconnected/reset in chat %s: %s", chat_id, reason)

    @staticmethod
    def _is_non_retryable_join_error(exc: Exception) -> bool:
        text = str(exc)
        return "GROUPCALL_INVALID" in text or "phone.JoinGroupCall" in text

    @staticmethod
    def _stream_source(track: Track) -> str:
        return track.stream_url or track.file_path or track.webpage_url

    @staticmethod
    def _ffmpeg_parameters() -> str:
        return "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -vn -loglevel error"

    def _build_media_stream(self, track: Track) -> MediaStream:
        source = self._stream_source(track)
        if track.stream_url:
            return MediaStream(
                source,
                ffmpeg_parameters=self._ffmpeg_parameters(),
            )
        return MediaStream(
            source,
            ffmpeg_parameters=self._ffmpeg_parameters(),
            ytdlp_parameters="--no-playlist --quiet --format bestaudio/best --geo-bypass --nocheckcertificate",
        )

    def _register_stream_end_handler(self) -> None:
        try:
            @self.calls.on_update(filters.stream_end())
            async def _on_stream_end(_, update: Any) -> None:
                chat_id = int(getattr(update, "chat_id"))
                now = time.monotonic()
                started_at = self._started_at.get(chat_id, 0.0)
                if started_at and (now - started_at) < 2.0:
                    self.logger.warning(
                        "Ignoring early stream_end for chat %s (elapsed=%.2fs)",
                        chat_id,
                        now - started_at,
                    )
                    return
                self.logger.info("Received stream_end for chat %s", chat_id)
                if not await self._is_connected(chat_id):
                    await self._reset_state(chat_id, "stream_end while disconnected", keep_queue=True)
                    return
                await self.play_next(chat_id, notify_ui=True)
        except Exception as exc:  # pragma: no cover
            self.logger.warning("Unable to register stream-end handler: %s", exc)

    async def _start_stream(self, chat_id: int, track: Track, retry: int = 2) -> None:
        was_connected = await self._is_connected(chat_id)

        stream = self._build_media_stream(track)
        for attempt in range(retry + 1):
            try:
                self.logger.info(
                    "Starting stream in chat %s (attempt=%s) file=%s",
                    chat_id,
                    attempt + 1,
                    self._stream_source(track),
                )
                await self.calls.play(chat_id, stream)
                joined = False
                for _ in range(6):
                    active_group_calls = await self.calls.group_calls
                    if chat_id in active_group_calls:
                        joined = True
                        break
                    await asyncio.sleep(0.5)

                if not joined:
                    active_group_calls = await self.calls.group_calls
                    self.logger.warning(
                        "Join not confirmed for chat %s. Active group calls: %s",
                        chat_id,
                        list(active_group_calls.keys()),
                    )
                    raise RuntimeError(
                        "Voice chat join was not confirmed. Ensure the session account can join/speak in this call."
                    )

                self._started_at[chat_id] = time.monotonic()
                self._paused_total[chat_id] = 0.0
                self._paused_at.pop(chat_id, None)
                self._active_chats.add(chat_id)
                if not was_connected:
                    self.logger.info("VC reconnected in chat %s", chat_id)
                self.logger.info("Stream started in chat %s", chat_id)
                return
            except Exception as exc:
                self.logger.warning(
                    "Stream start failed in chat %s on attempt %s: %s",
                    chat_id,
                    attempt + 1,
                    exc,
                )
                if self._is_non_retryable_join_error(exc):
                    await self._reset_state(chat_id, "join_group_call rejected", keep_queue=True)
                    raise RuntimeError(
                        "No active voice chat found in this group. Start the group call first, then use /play again."
                    ) from exc
                if attempt >= retry:
                    await self._reset_state(chat_id, "stream start retries exhausted", keep_queue=True)
                    raise exc
                await asyncio.sleep(1.2 * (attempt + 1))

    async def play_next(self, chat_id: int, notify_ui: bool = False) -> Track | None:
        async with self._lock:
            previous = await self.queue.get_current(chat_id)
            repeat = await self.queue.get_repeat(chat_id)

            if previous and repeat:
                track = previous
            else:
                if previous and not repeat:
                    await self._safe_cleanup(previous)
                track = await self.queue.pop_next(chat_id)

            if not track:
                await self._reset_state(chat_id, "queue empty", keep_queue=True)
                try:
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass
                self.logger.info("Queue empty; left call in chat %s", chat_id)
                if notify_ui:
                    await self._notify_auto_transition(chat_id, None)
                return None

            try:
                if not track.file_path:
                    try:
                        await self._start_stream(chat_id, track)
                    except Exception:
                        track = await self.ytdlp.download_audio(track)
                        await self._start_stream(chat_id, track)
                else:
                    await self._start_stream(chat_id, track)
            except Exception:
                if not repeat:
                    await self.queue.enqueue_front(chat_id, track)
                raise

            await self.queue.set_current(chat_id, track)
            self.logger.info("Now playing in chat %s: %s", chat_id, track.title)
            if notify_ui:
                await self._notify_auto_transition(chat_id, track)
            return track

    async def enqueue_or_play(self, chat_id: int, track: Track) -> tuple[str, int]:
        position = await self.queue.enqueue(chat_id, track)
        current = await self.queue.get_current(chat_id)
        connected = await self._is_connected(chat_id)
        is_playing = bool(current and chat_id in self._active_chats)

        if not connected and current:
            await self._reset_state(chat_id, "stale player state detected", keep_queue=True)
            current = None
            is_playing = False

        if is_playing and connected:
            return "queued", position

        started = await self.play_next(chat_id)
        if started:
            self.logger.info("Playback restarted in chat %s", chat_id)
            return "playing", 1
        return "queued", position

    async def pause(self, chat_id: int) -> None:
        await self.calls.pause(chat_id)
        if chat_id in self._started_at and chat_id not in self._paused_at:
            self._paused_at[chat_id] = time.monotonic()

    async def resume(self, chat_id: int) -> None:
        await self.calls.resume(chat_id)
        paused_at = self._paused_at.pop(chat_id, None)
        if paused_at is not None:
            self._paused_total[chat_id] = self._paused_total.get(chat_id, 0.0) + (
                time.monotonic() - paused_at
            )

    async def skip(self, chat_id: int) -> Track | None:
        current = await self.queue.get_current(chat_id)
        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            pass

        await self._safe_cleanup(current)
        await self._reset_state(chat_id, "skip requested", keep_queue=True)
        return await self.play_next(chat_id)

    async def stop(self, chat_id: int) -> None:
        current = await self.queue.get_current(chat_id)
        queued = await self.queue.clear(chat_id)

        try:
            await self.calls.leave_call(chat_id)
        except Exception:
            pass

        for item in queued:
            await self._safe_cleanup(item)
        await self._safe_cleanup(current)

        await self._reset_state(chat_id, "stop requested", keep_queue=False)

    async def set_volume(self, chat_id: int, volume: int) -> int:
        value = await self.queue.set_volume(chat_id, volume)
        await self.calls.change_volume_call(chat_id, value)
        return value

    async def get_elapsed(self, chat_id: int) -> int:
        started = self._started_at.get(chat_id)
        if started is None:
            return 0
        elapsed = time.monotonic() - started
        elapsed -= self._paused_total.get(chat_id, 0.0)
        paused_at = self._paused_at.get(chat_id)
        if paused_at is not None:
            elapsed -= max(0.0, time.monotonic() - paused_at)
        return max(0, int(elapsed))
