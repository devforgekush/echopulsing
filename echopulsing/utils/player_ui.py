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
    track_signature: str


class PlayerUI:
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
        total_blocks = 10
        if not duration or duration <= 0:
            return "▰" * total_blocks
        ratio = max(0.0, min(1.0, elapsed / duration))
        filled = int(round(ratio * total_blocks))
        filled = max(0, min(total_blocks, filled))
        return ("▰" * filled) + ("▱" * (total_blocks - filled))

    @staticmethod
    def _trim_text(text: str | None, max_len: int) -> str:
        value = (text or "").strip()
        if len(value) <= max_len:
            return value
        return value[: max(0, max_len - 3)].rstrip() + "..."

    @staticmethod
    def _loop_label(mode: str) -> str:
        if mode == "single":
            return "🔁 Single"
        if mode == "all":
            return "🔁 All"
        return "🔁 Off"

    async def controls_markup(self, chat_id: int) -> InlineKeyboardMarkup:
        paused = await self.runtime.voice.is_paused(chat_id)
        loop_mode = await self.runtime.queue.get_loop_mode(chat_id)
        play_pause = "▶️ Play" if paused else "⏸ Pause"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(play_pause, callback_data="player:toggle"),
                    InlineKeyboardButton("⏭ Skip", callback_data="player:skip"),
                ],
                [
                    InlineKeyboardButton(self._loop_label(loop_mode), callback_data="player:loop"),
                    InlineKeyboardButton("🔀 Shuffle", callback_data="player:shuffle"),
                ],
            ]
        )

    async def loading_animation(self, message: Message) -> Message | None:
        sequence = [
            "⚡ Initializing stream...",
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

    async def _build_body(self, chat_id: int, track: Track) -> str:
        elapsed = await self.runtime.voice.get_elapsed(chat_id)
        duration = track.duration
        current_time = format_seconds(elapsed)
        duration_text = format_seconds(duration)
        volume = await self.runtime.queue.get_volume(chat_id)

        title = _escape_html(self._trim_text(track.title, 80))
        requester_name = _escape_html(self._trim_text(track.requester_name, 40))

        return (
            "🎧 EchoPulsing Music\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🎵 <b>{title}</b>\n"
            f"👤 Requested by: {requester_name}\n"
            f"⏱ {current_time} / {duration_text}\n\n"
            f"{self._progress_bar(elapsed, duration)}\n\n"
            f"🔊 Volume: {volume}%\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "SOURCE: YouTube"
        )

    async def _delete_previous(self, chat_id: int) -> None:
        previous = self._last_now_playing.get(chat_id)
        if not previous:
            return
        try:
            await self.bot.delete_messages(chat_id, previous.message_id)
        except Exception:
            pass

    async def show_now_playing(self, chat_id: int, track: Track) -> None:
        async with self._locks[chat_id]:
            await self._delete_previous(chat_id)
            body = await self._build_body(chat_id, track)
            markup = await self.controls_markup(chat_id)
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
                track_signature=self._track_signature(track),
            )

        self._ensure_progress_task(chat_id)

    async def refresh_now_playing(self, chat_id: int, force: bool = False) -> None:
        current = await self.runtime.queue.get_current(chat_id)
        if not current:
            await self.clear_now_playing(chat_id)
            return

        ref = self._last_now_playing.get(chat_id)
        signature = self._track_signature(current)
        if not ref or signature != ref.track_signature:
            await self.show_now_playing(chat_id, current)
            return

        body = await self._build_body(chat_id, current)
        if not force and body == ref.last_body:
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
                            reply_markup=await self.controls_markup(chat_id),
                        )
                    else:
                        await self.bot.edit_message_text(
                            chat_id,
                            ref.message_id,
                            text=body,
                            parse_mode=enums.ParseMode.HTML,
                            reply_markup=await self.controls_markup(chat_id),
                        )
                    ref.last_body = body
                except Exception as exc:
                    if "MESSAGE_NOT_MODIFIED" in str(exc):
                        return
                    resend_required = True
                    await self._delete_previous(chat_id)
                    self._last_now_playing.pop(chat_id, None)

        if resend_required:
            await self.show_now_playing(chat_id, current)

    async def clear_now_playing(self, chat_id: int) -> None:
        task = self._progress_tasks.pop(chat_id, None)
        current_task = asyncio.current_task()
        if task and not task.done() and task is not current_task:
            task.cancel()

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
        self._progress_tasks[chat_id] = asyncio.create_task(self._progress_loop(chat_id))

    async def _progress_loop(self, chat_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(18)
                current = await self.runtime.queue.get_current(chat_id)
                if not current:
                    await self.clear_now_playing(chat_id)
                    return
                await self.refresh_now_playing(chat_id)
        except asyncio.CancelledError:
            return
        except Exception:
            return


def _escape_html(text: str | None) -> str:
    return html.escape(text or "", quote=False)