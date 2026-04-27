from __future__ import annotations

import asyncio
import html
from collections import defaultdict
from dataclasses import dataclass

from pyrogram import Client, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from echopulsing.services.models import Track
from echopulsing.utils.helpers import format_seconds


@dataclass(slots=True)
class _NowPlayingRef:
    message_id: int
    is_photo: bool
    last_body: str
    last_controls_signature: str
    track_signature: str
    mode: str


class PlayerUI:
    _PROGRESS_TICKS = 11
    _PROGRESS_REFRESH_SECONDS = 5

    def __init__(self, bot: Client, runtime: object) -> None:
        self.bot = bot
        self.runtime = runtime
        self._last_now_playing: dict[int, _NowPlayingRef] = {}
        self._progress_tasks: dict[int, asyncio.Task[None]] = {}
        self._locks = defaultdict(asyncio.Lock)

    @staticmethod
    def _track_signature(track: Track) -> str:
        return f"{track.id or ''}:{track.title}:{track.created_at}"

    @staticmethod
    def _progress_bar(elapsed: int, duration: int | None) -> str:
        width = PlayerUI._PROGRESS_TICKS
        if width <= 1:
            return "●"
        if not duration or duration <= 0:
            pointer = width // 2
        else:
            ratio = max(0.0, min(1.0, elapsed / duration))
            pointer = int(round(ratio * (width - 1)))
        chars = ["─"] * width
        chars[pointer] = "●"
        return "".join(chars)

    @staticmethod
    def _trim_text(text: str | None, max_len: int) -> str:
        value = (text or "").strip()
        if len(value) <= max_len:
            return value
        return value[: max(0, max_len - 3)].rstrip() + "..."

    @staticmethod
    def _loop_label(mode: str) -> str:
        if mode == "single":
            return "🔁 Loop: 1"
        if mode == "all":
            return "🔁 Loop: All"
        return "🔁 Loop: Off"

    @staticmethod
    def _controls_signature(paused: bool, loop_mode: str) -> str:
        return f"paused={int(paused)}|loop={loop_mode}"

    @staticmethod
    def _progress_line(elapsed: int, duration: int | None) -> str:
        remaining = max(0, (duration or 0) - elapsed)
        elapsed_text = format_seconds(elapsed)
        remaining_text = f"-{format_seconds(remaining)}" if duration and duration > 0 else "-live"
        return f"{elapsed_text} {PlayerUI._progress_bar(elapsed, duration)} {remaining_text}"

    async def controls_markup(self, chat_id: int) -> InlineKeyboardMarkup:
        paused = await self.runtime.voice.is_paused(chat_id)
        loop_mode = await self.runtime.queue.get_loop_mode(chat_id)
        play_pause = "⏯ Resume" if paused else "⏯ Pause"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(play_pause, callback_data="player:toggle"),
                    InlineKeyboardButton("⏭ Skip", callback_data="player:skip"),
                    InlineKeyboardButton(self._loop_label(loop_mode), callback_data="player:loop"),
                ],
                [
                    InlineKeyboardButton("🔀 Shuffle", callback_data="player:shuffle"),
                    InlineKeyboardButton("⏹ Stop", callback_data="player:stop"),
                ],
            ]
        )

    async def _controls_state(self, chat_id: int) -> tuple[InlineKeyboardMarkup, str]:
        paused = await self.runtime.voice.is_paused(chat_id)
        loop_mode = await self.runtime.queue.get_loop_mode(chat_id)
        return await self.controls_markup(chat_id), self._controls_signature(paused, loop_mode)

    async def loading_animation(self, message: Message) -> Message | None:
        sequence = [
            "⚡ Initializing stream...",
            "🤖 Ensuring assistant is connected...",
            "🔄 Connecting to voice chat...",
            "🎶 Fetching track...",
            "▶️ Starting playback...",
        ]
        try:
            status = await message.reply_text(sequence[0])
        except Exception:
            return None
        for step in sequence[1:]:
            await asyncio.sleep(0.85)
            try:
                await status.edit_text(step)
            except Exception:
                break
        return status

    async def _get_assistant_status(self, chat_id: int) -> str:
        """Get assistant status display."""
        try:
            in_chat = await self.runtime.assistant.is_in_chat(chat_id)
            if in_chat:
                label = self.runtime.assistant.assistant_label
                return f"🤖 <b>Assistant:</b> <code>Connected ({label})</code>"
            else:
                return "🤖 <b>Assistant:</b> <code>Joining...</code>"
        except Exception:
            return ""

    async def _build_body(self, chat_id: int, track: Track, mode: str = "normal") -> str:
        duration = track.duration

        if mode == "force":
            title = _escape_html(self._trim_text(track.title, 60))
            requester_name = _escape_html(self._trim_text(track.requester_name, 40))
            duration_text = _escape_html(format_seconds(duration))
            assistant_status = await self._get_assistant_status(chat_id)
            body = (
                "<b>🎵 Force Playing</b>\n\n"
                f"<b>▶️ Title:</b> <code>{title}</code>\n"
                f"<b>⏱ Duration:</b> <code>{duration_text}</code>\n"
                f"<b>👤 Requested by:</b> <code>{requester_name}</code>"
            )
            if assistant_status:
                body += f"\n\n{assistant_status}"
            return body

        elapsed = await self.runtime.voice.get_elapsed(chat_id)
        title = _escape_html(self._trim_text(track.title, 70))
        requester_name = _escape_html(self._trim_text(track.requester_name, 40))
        progress = _escape_html(self._progress_line(elapsed, duration))
        assistant_status = await self._get_assistant_status(chat_id)

        body = (
            "🎵 <b>Now Playing</b>\n\n"
            f"▶️ <b>Title:</b> <code>{title}</code>\n"
            f"👤 <b>Requested by:</b> <code>{requester_name}</code>\n\n"
            "⏳ <b>Progress:</b>\n"
            f"<code>{progress}</code>"
        )
        if assistant_status:
            body += f"\n\n{assistant_status}"
        return body

    async def _delete_previous(self, chat_id: int) -> None:
        previous = self._last_now_playing.get(chat_id)
        if not previous:
            return
        try:
            await self.bot.delete_messages(chat_id, previous.message_id)
        except Exception:
            pass

    def _cancel_progress_task(self, chat_id: int) -> None:
        task = self._progress_tasks.get(chat_id)
        current_task = asyncio.current_task()
        if task and not task.done() and task is not current_task:
            task.cancel()
        self._progress_tasks.pop(chat_id, None)

    async def show_now_playing(self, chat_id: int, track: Track, mode: str = "normal") -> None:
        await self._show_track_message(chat_id, track, mode=mode)

    async def _show_track_message(self, chat_id: int, track: Track, mode: str) -> None:
        self._cancel_progress_task(chat_id)
        async with self._locks[chat_id]:
            await self._delete_previous(chat_id)
            body = await self._build_body(chat_id, track, mode=mode)
            markup, controls_signature = await self._controls_state(chat_id)
            sent: Message
            is_photo = False
            if track.thumbnail:
                try:
                    sent = await self.bot.send_photo(
                        chat_id,
                        photo=track.thumbnail,
                        caption=body,
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                    is_photo = True
                except Exception:
                    sent = await self.bot.send_message(
                        chat_id,
                        text=body,
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
            else:
                sent = await self.bot.send_message(
                    chat_id,
                    text=body,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=markup,
                )

            self._last_now_playing[chat_id] = _NowPlayingRef(
                message_id=sent.id,
                is_photo=is_photo,
                last_body=body,
                last_controls_signature=controls_signature,
                track_signature=self._track_signature(track),
                mode=mode,
            )

        self._ensure_progress_task(chat_id)

    async def refresh_now_playing(self, chat_id: int, force: bool = False) -> None:
        current = await self.runtime.queue.get_current(chat_id)
        if not current:
            await self.clear_now_playing(chat_id)
            return

        ref = self._last_now_playing.get(chat_id)
        mode = ref.mode if ref else "normal"
        signature = self._track_signature(current)
        if not ref or signature != ref.track_signature:
            self._cancel_progress_task(chat_id)
            await self.show_now_playing(chat_id, current, mode=mode)
            return

        body = await self._build_body(chat_id, current, mode=mode)
        markup, controls_signature = await self._controls_state(chat_id)
        if body == ref.last_body and controls_signature == ref.last_controls_signature:
            return

        resend_required = False
        async with self._locks[chat_id]:
            ref = self._last_now_playing.get(chat_id)
            if not ref or signature != ref.track_signature:
                resend_required = True
            else:
                try:
                    if ref.is_photo:
                        await self.bot.edit_message_caption(
                            chat_id,
                            ref.message_id,
                            caption=body,
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=markup,
                        )
                    else:
                        await self.bot.edit_message_text(
                            chat_id,
                            ref.message_id,
                            text=body,
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=markup,
                        )
                    ref.last_body = body
                    ref.last_controls_signature = controls_signature
                except Exception as exc:
                    if "MESSAGE_NOT_MODIFIED" in str(exc):
                        return
                    resend_required = True
                    await self._delete_previous(chat_id)
                    self._last_now_playing.pop(chat_id, None)

        if resend_required:
            await self.show_now_playing(chat_id, current, mode=mode)

    async def clear_now_playing(self, chat_id: int) -> None:
        self._cancel_progress_task(chat_id)

        async with self._locks[chat_id]:
            ref = self._last_now_playing.pop(chat_id, None)
            if not ref:
                return
            try:
                await self.bot.delete_messages(chat_id, ref.message_id)
            except Exception:
                pass

    def _ensure_progress_task(self, chat_id: int) -> None:
        task = self._progress_tasks.get(chat_id)
        if task and not task.done():
            return
        self._progress_tasks.pop(chat_id, None)
        self._progress_tasks[chat_id] = asyncio.create_task(self._progress_loop(chat_id))

    async def _progress_loop(self, chat_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._PROGRESS_REFRESH_SECONDS)
                current = await self.runtime.queue.get_current(chat_id)
                if not current:
                    await self.clear_now_playing(chat_id)
                    return
                await self.refresh_now_playing(chat_id)
        except asyncio.CancelledError:
            return
        except Exception:
            return
        finally:
            self._progress_tasks.pop(chat_id, None)


def _escape_html(text: str | None) -> str:
    return html.escape(text or "", quote=False)