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
    _AUTO_LEAVE_DELAY_SECONDS = 60
    _AUTO_LEAVE_CHECK_INTERVAL_SECONDS = 5

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
        self._started_at: dict[int, float] = {}
        self._paused_at: dict[int, float] = {}
        self._paused_total: dict[int, float] = {}
        self._active_chats: set[int] = set()
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._prefetch_cache: dict[int, Track] = {}
        self._prefetch_tasks: dict[int, asyncio.Task[None]] = {}
        self._auto_leave_tasks: dict[int, asyncio.Task[None]] = {}
        self._auto_transition_callback: Callable[[int, Track | None], Awaitable[None]] | None = None
        self._register_stream_end_handler()

    @staticmethod
    def _track_signature(track: Track | None) -> str:
        if track is None:
            return ""
        return f"{track.id or ''}:{track.webpage_url}:{track.created_at}"

    async def invalidate_prefetch(self, chat_id: int) -> None:
        self._prefetch_cache.pop(chat_id, None)
        task = self._prefetch_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_prefetch(self, chat_id: int) -> None:
        task = self._prefetch_tasks.get(chat_id)
        if task and not task.done():
            return
        self._prefetch_tasks[chat_id] = asyncio.create_task(self._prefetch_next(chat_id))

    async def _prefetch_next(self, chat_id: int) -> None:
        try:
            async with self._chat_lock(chat_id):
                next_track = await self.queue.peek_next(chat_id)
                if not next_track:
                    self._prefetch_cache.pop(chat_id, None)
                    return

                next_signature = self._track_signature(next_track)
                cached = self._prefetch_cache.get(chat_id)
                if cached and self._track_signature(cached) == next_signature and cached.stream_url:
                    return

            if next_track.stream_url:
                resolved = next_track
            else:
                resolved = await self.ytdlp.ensure_stream_url(next_track)

            if not resolved.stream_url:
                return

            async with self._chat_lock(chat_id):
                still_next = await self.queue.peek_next(chat_id)
                if not still_next:
                    self._prefetch_cache.pop(chat_id, None)
                    return
                if self._track_signature(still_next) != next_signature:
                    return
                self._prefetch_cache[chat_id] = resolved
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.debug("Prefetch failed in chat %s: %s", chat_id, exc)
        finally:
            task = self._prefetch_tasks.get(chat_id)
            if task and task.done():
                self._prefetch_tasks.pop(chat_id, None)

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
        return

    def _chat_lock(self, chat_id: int) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    async def _group_call_is_active(self, chat_id: int) -> bool:
        try:
            active_group_calls = await self.calls.group_calls
        except Exception:
            return False
        return chat_id in active_group_calls

    async def _is_connected(self, chat_id: int) -> bool:
        return await self._group_call_is_active(chat_id)

    async def _participant_count(self, chat_id: int) -> int | None:
        for attempt in range(2):
            try:
                if hasattr(self.calls, "get_participants"):
                    participants = await self.calls.get_participants(chat_id)
                    return len(list(participants))
                if hasattr(self.calls, "get_group_call_participants"):
                    participants = await self.calls.get_group_call_participants(chat_id)
                    return len(list(participants))
            except Exception as exc:
                self.logger.debug(
                    "Could not fetch VC participants in chat %s (attempt %s): %s",
                    chat_id,
                    attempt + 1,
                    exc,
                )
                if attempt == 0:
                    continue
                return None
        return None

    async def _is_only_bot_in_vc(self, chat_id: int) -> bool:
        if not await self._is_connected(chat_id):
            return False
        participant_count = await self._participant_count(chat_id)
        if participant_count is None:
            return False
        return participant_count <= 1

    def _cancel_auto_leave_timer(self, chat_id: int, reason: str = "") -> None:
        task = self._auto_leave_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
            if reason:
                self.logger.info("Auto-leave timer canceled in chat %s: %s", chat_id, reason)

    async def _refresh_auto_leave_watch(self, chat_id: int) -> None:
        if not await self._is_connected(chat_id):
            self._cancel_auto_leave_timer(chat_id, "voice chat disconnected")
            return

        if not await self._is_only_bot_in_vc(chat_id):
            self._cancel_auto_leave_timer(chat_id, "participants detected")
            return

        task = self._auto_leave_tasks.get(chat_id)
        if task and not task.done():
            return

        self.logger.info("Auto-leave timer started in chat %s (delay=%ss)", chat_id, self._AUTO_LEAVE_DELAY_SECONDS)
        self._auto_leave_tasks[chat_id] = asyncio.create_task(self._auto_leave_when_empty(chat_id))

    async def _auto_leave_when_empty(self, chat_id: int) -> None:
        deadline = time.monotonic() + self._AUTO_LEAVE_DELAY_SECONDS
        try:
            while True:
                if not await self._is_connected(chat_id):
                    return

                if not await self._is_only_bot_in_vc(chat_id):
                    self.logger.info("Auto-leave canceled in chat %s: participants joined", chat_id)
                    return

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                await asyncio.sleep(min(self._AUTO_LEAVE_CHECK_INTERVAL_SECONDS, remaining))

            async with self._chat_lock(chat_id):
                if not await self._is_connected(chat_id):
                    return
                if not await self._is_only_bot_in_vc(chat_id):
                    self.logger.info("Auto-leave aborted in chat %s: participants joined", chat_id)
                    return

                try:
                    await self.calls.leave_call(chat_id)
                except Exception:
                    pass

                await self._reset_state(chat_id, "auto-leave: alone in VC for 60s", keep_queue=True)
                await self._notify_auto_transition(chat_id, None)
                self.logger.info("Auto-left voice chat %s after 60s with no participants", chat_id)
        except asyncio.CancelledError:
            return
        finally:
            task = self._auto_leave_tasks.get(chat_id)
            if task and task.done():
                self._auto_leave_tasks.pop(chat_id, None)

    async def _reset_state(self, chat_id: int, reason: str, keep_queue: bool = True) -> None:
        self._cancel_auto_leave_timer(chat_id, f"state reset: {reason}")
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
        if track.stream_url:
            return track.stream_url
        raise ValueError("Track is missing a direct stream URL")

    @staticmethod
    def _ffmpeg_parameters() -> str:
        return (
            "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 2 "
            "-fflags nobuffer -flags low_delay -vn -loglevel error"
        )

    def _build_media_stream(self, track: Track) -> MediaStream:
        return MediaStream(self._stream_source(track), ffmpeg_parameters=self._ffmpeg_parameters())

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
                try:
                    await self.play_next(chat_id, notify_ui=True)
                except Exception as exc:
                    self.logger.exception(
                        "Auto transition failed after stream_end in chat %s: %s",
                        chat_id,
                        exc,
                    )
        except Exception as exc:  # pragma: no cover
            self.logger.warning("Unable to register stream-end handler: %s", exc)

    async def prewarm_connection(self, chat_id: int) -> None:
        # Keep state consistent while yt-dlp resolution runs in parallel.
        connected = await self._is_connected(chat_id)
        current = await self.queue.get_current(chat_id)
        if current and not connected:
            await self._reset_state(chat_id, "stale state found during prewarm", keep_queue=True)

    async def _start_stream(self, chat_id: int, track: Track, retry: int = 1) -> None:
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

                self._started_at[chat_id] = time.monotonic()
                self._paused_total[chat_id] = 0.0
                self._paused_at.pop(chat_id, None)
                self._active_chats.add(chat_id)
                if not was_connected:
                    self.logger.info("VC reconnected in chat %s", chat_id)
                self.logger.info("Stream started in chat %s", chat_id)
                await self._refresh_auto_leave_watch(chat_id)
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
                await asyncio.sleep(0.35 * (attempt + 1))

    async def _play_next_locked(
        self,
        chat_id: int,
        notify_ui: bool = False,
        force_advance: bool = False,
    ) -> Track | None:
        previous = await self.queue.get_current(chat_id)
        loop_mode = await self.queue.get_loop_mode(chat_id)

        if previous and loop_mode == "single" and not force_advance:
            track = previous
        else:
            if previous and loop_mode == "all":
                await self.queue.enqueue(chat_id, previous)
            if previous and loop_mode != "single":
                await self._safe_cleanup(previous)
            track = await self.queue.pop_next(chat_id)

        cached = self._prefetch_cache.get(chat_id)
        if cached and track and self._track_signature(cached) == self._track_signature(track):
            track = cached
            self._prefetch_cache.pop(chat_id, None)

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
            if not track.stream_url:
                track = await self.ytdlp.ensure_stream_url(track)
            if not track.stream_url:
                raise RuntimeError("Could not resolve a direct stream URL for playback")
            await self._start_stream(chat_id, track)
        except Exception:
            if loop_mode != "single":
                await self.queue.enqueue_front(chat_id, track)
            raise

        await self.queue.set_current(chat_id, track)
        self.logger.info("Now playing in chat %s: %s", chat_id, track.title)
        if notify_ui:
            await self._notify_auto_transition(chat_id, track)
        self._schedule_prefetch(chat_id)
        return track

    async def play_next(self, chat_id: int, notify_ui: bool = False) -> Track | None:
        async with self._chat_lock(chat_id):
            return await self._play_next_locked(chat_id, notify_ui=notify_ui)

    async def enqueue_or_play(self, chat_id: int, track: Track) -> tuple[str, int]:
        async with self._chat_lock(chat_id):
            position = await self.queue.enqueue(chat_id, track)
            current = await self.queue.get_current(chat_id)
            connected = await self._is_connected(chat_id)
            is_playing = bool(current and chat_id in self._active_chats)

            if connected:
                await self._refresh_auto_leave_watch(chat_id)

            if not connected and current:
                await self._reset_state(chat_id, "stale player state detected", keep_queue=True)
                current = None
                is_playing = False

            if is_playing and connected:
                return "queued", position

            started = await self._play_next_locked(chat_id)
            if started:
                self.logger.info("Playback restarted in chat %s", chat_id)
                return "playing", 1
            return "queued", position

    async def force_play(self, chat_id: int, new_track: Track) -> Track | None:
        async with self._chat_lock(chat_id):
            current = await self.queue.get_current(chat_id)

            await self.invalidate_prefetch(chat_id)

            if current is not None:
                await self.queue.enqueue_front(chat_id, current)

            try:
                await self.calls.leave_call(chat_id)
            except Exception:
                pass

            await self._reset_state(chat_id, "force play", keep_queue=True)
            await self.queue.enqueue_front(chat_id, new_track)

            started = await self._play_next_locked(chat_id)
            if started is not None:
                self._schedule_prefetch(chat_id)
            return started

    async def pause(self, chat_id: int) -> None:
        await self.calls.pause(chat_id)
        if chat_id in self._started_at and chat_id not in self._paused_at:
            self._paused_at[chat_id] = time.monotonic()

    async def is_paused(self, chat_id: int) -> bool:
        return chat_id in self._paused_at

    async def toggle_pause(self, chat_id: int) -> bool:
        if await self.is_paused(chat_id):
            await self.resume(chat_id)
            return False
        await self.pause(chat_id)
        return True

    async def resume(self, chat_id: int) -> None:
        await self.calls.resume(chat_id)
        paused_at = self._paused_at.pop(chat_id, None)
        if paused_at is not None:
            self._paused_total[chat_id] = self._paused_total.get(chat_id, 0.0) + (
                time.monotonic() - paused_at
            )

    async def skip(self, chat_id: int) -> Track | None:
        async with self._chat_lock(chat_id):
            await self.invalidate_prefetch(chat_id)
            current = await self.queue.get_current(chat_id)
            try:
                await self.calls.leave_call(chat_id)
            except Exception:
                pass

            await self._safe_cleanup(current)
            await self._reset_state(chat_id, "skip requested", keep_queue=True)
            return await self._play_next_locked(chat_id, force_advance=True)

    async def stop(self, chat_id: int) -> None:
        async with self._chat_lock(chat_id):
            await self.invalidate_prefetch(chat_id)
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
