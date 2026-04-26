from __future__ import annotations

import asyncio
import html
import time
from typing import TYPE_CHECKING

from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, Message

from echopulsing.services.models import Track
from echopulsing.utils.helpers import command_arg, format_seconds, is_admin

if TYPE_CHECKING:
    from echopulsing.services.runtime import Runtime


_RECENT_PLAY_MESSAGES: dict[tuple[int, int], float] = {}


def _display_name(message: Message) -> str:
    if message.from_user:
        return message.from_user.first_name
    return "Anonymous"


def _escape(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def _track_card(track: Track, header: str) -> str:
    title = _escape(track.title)
    requester = _escape(track.requester_name)
    duration = format_seconds(track.duration)
    return (
        f"🎵 <b>{header}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Title:</b> <code>{title}</code>\n"
        f"• <b>Duration:</b> <code>{duration}</code>\n"
        f"• <b>Requested by:</b> <code>{requester}</code>"
    )


def _is_no_results_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no results" in text or "invalid youtube link" in text


def _should_ignore_duplicate_play(chat_id: int, message_id: int) -> bool:
    now = time.monotonic()
    key = (chat_id, message_id)
    cutoff = now - 10.0
    stale_keys = [item for item, seen_at in _RECENT_PLAY_MESSAGES.items() if seen_at < cutoff]
    for stale_key in stale_keys:
        _RECENT_PLAY_MESSAGES.pop(stale_key, None)
    if key in _RECENT_PLAY_MESSAGES:
        return True
    _RECENT_PLAY_MESSAGES[key] = now
    return False


async def _require_admin(client: Client, message: Message) -> bool:
    if not message.from_user:
        await message.reply_text("Only real user accounts can run this command.")
        return False
    if await is_admin(client, message.chat.id, message.from_user.id):
        return True
    await message.reply_text("Only group admins can use this control command.")
    return False


async def _require_admin_query(client: Client, query: CallbackQuery) -> bool:
    if not query.message or not query.from_user:
        await _safe_answer_query(query, "Only real user accounts can use this control.", show_alert=True)
        return False
    if await is_admin(client, query.message.chat.id, query.from_user.id):
        return True
    await _safe_answer_query(query, "Only group admins can use this control command.", show_alert=True)
    return False


async def _safe_answer_query(query: CallbackQuery, text: str, *, show_alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=show_alert)
    except Exception as exc:
        if "QUERY_ID_INVALID" not in str(exc):
            raise


def register(app: Client, runtime: Runtime) -> None:
    @app.on_message(filters.command(["play"]) & filters.group)
    async def play_handler(client: Client, message: Message) -> None:
        if not runtime.voice_available:
            await message.reply_text(
                "Voice backend is unavailable on this host. Run the bot in Linux (Docker/WSL/VPS)."
            )
            return

        if _should_ignore_duplicate_play(message.chat.id, message.id):
            return

        query = command_arg(message)
        if not query:
            await message.reply_text("Usage: /play <song name or YouTube URL>")
            return

        requester_id = message.from_user.id if message.from_user else 0
        requester_name = _display_name(message)
        resolve_task = asyncio.create_task(runtime.ytdlp.resolve(query, requester_id, requester_name))
        status_message = await runtime.ui.loading_animation(message)

        try:
            track = await resolve_task
            state, position = await runtime.voice.enqueue_or_play(message.chat.id, track)

            if state == "playing":
                await runtime.ui.show_now_playing(message.chat.id, track)
            else:
                await message.reply_text(
                    f"🎶 Queued at position #{position}: {_escape(track.title)}",
                    parse_mode=enums.ParseMode.HTML,
                )
        except ValueError as exc:
            if _is_no_results_error(exc):
                await message.reply_text("⚠️ No results found")
            else:
                await message.reply_text("❌ Failed to play track")
            await runtime.log_event(f"/play failed in {message.chat.id}: {exc}")
        except Exception as exc:
            await message.reply_text("❌ Failed to play track")
            await runtime.log_event(f"/play failed in {message.chat.id}: {exc}")
        finally:
            if status_message is not None:
                try:
                    await status_message.delete()
                except Exception:
                    pass

    @app.on_message(filters.command(["pause", "hold"]) & filters.group)
    async def pause_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return
        try:
            await runtime.voice.pause(message.chat.id)
            await runtime.ui.refresh_now_playing(message.chat.id, force=True)
            await message.reply_text("Playback paused.")
        except Exception as exc:
            await runtime.log_event(f"/pause failed in {message.chat.id}: {exc}")
            await message.reply_text(f"Pause failed: {exc}")

    @app.on_message(filters.command(["resume", "unpause", "continue"]) & filters.group)
    async def resume_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return
        try:
            await runtime.voice.resume(message.chat.id)
            await runtime.ui.refresh_now_playing(message.chat.id, force=True)
            await message.reply_text("Playback resumed.")
        except Exception as exc:
            await runtime.log_event(f"/resume failed in {message.chat.id}: {exc}")
            await message.reply_text(f"Resume failed: {exc}")

    @app.on_message(filters.command(["skip"]) & filters.group)
    async def skip_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return
        try:
            next_track = await runtime.voice.skip(message.chat.id)
            if next_track:
                await runtime.ui.show_now_playing(message.chat.id, next_track)
                await message.reply_text(f"Skipped. Now playing: {next_track.title}")
            else:
                await runtime.ui.clear_now_playing(message.chat.id)
                await message.reply_text("Skipped. Queue is now empty.")
        except Exception as exc:
            await runtime.log_event(f"/skip failed in {message.chat.id}: {exc}")
            await message.reply_text(f"Skip failed: {exc}")

    @app.on_message(filters.command(["stop", "end"]) & filters.group)
    async def stop_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return
        try:
            await runtime.voice.stop(message.chat.id)
            await runtime.ui.clear_now_playing(message.chat.id)
            await message.reply_text("Stopped playback and cleared queue.")
        except Exception as exc:
            await runtime.log_event(f"/stop failed in {message.chat.id}: {exc}")
            await message.reply_text(f"Stop failed: {exc}")

    @app.on_message(filters.command(["queue"]) & filters.group)
    async def queue_handler(client: Client, message: Message) -> None:
        current = await runtime.queue.get_current(message.chat.id)
        queued = await runtime.queue.list_queue(message.chat.id)

        lines = []
        if current:
            lines.append(
                f"🎧 <b>Current</b>: <code>{_escape(current.title)}</code>"
            )
            lines.append(f"⏱ <b>Duration</b>: <code>{format_seconds(current.duration)}</code>")
            lines.append(f"👤 <b>Requested by</b>: <code>{_escape(current.requester_name)}</code>")
        else:
            lines.append("🎧 <b>Current</b>: nothing playing")

        if not queued:
            lines.append("\n📭 <b>Queue</b>: empty")
        else:
            lines.append("\n📜 <b>Queue</b>")
            for idx, item in enumerate(queued[:10], start=1):
                lines.append(
                    f"{idx}. <code>{_escape(item.title)}</code> — <code>{format_seconds(item.duration)}</code>"
                )
            if len(queued) > 10:
                lines.append(f"… and {len(queued) - 10} more")

        await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command(["current"]) & filters.group)
    async def current_handler(client: Client, message: Message) -> None:
        current = await runtime.queue.get_current(message.chat.id)
        if not current:
            await message.reply_text("Nothing is playing right now.")
            return

        await runtime.ui.refresh_now_playing(message.chat.id, force=True)

    @app.on_message(filters.command(["vcdebug"]) & filters.group)
    async def vcdebug_handler(client: Client, message: Message) -> None:
        try:
            active_group_calls = await runtime.calls.group_calls if runtime.calls is not None else {}
            in_call = message.chat.id in active_group_calls
            current = await runtime.queue.get_current(message.chat.id)
            current_title = current.title if current else "none"
            await message.reply_text(
                "Voice debug\n"
                f"chat_id: {message.chat.id}\n"
                f"active_group_calls: {list(active_group_calls.keys())}\n"
                f"in_call: {in_call}\n"
                f"current_track: {current_title}"
            )
        except Exception as exc:
            await message.reply_text(f"vcdebug failed: {exc}")

    @app.on_message(filters.command(["loop"]) & filters.group)
    async def loop_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return

        arg = command_arg(message)
        if not arg or arg.lower() not in {"on", "off"}:
            enabled = await runtime.queue.get_repeat(message.chat.id)
            await message.reply_text(f"Loop is {'on' if enabled else 'off'}. Use /loop on or /loop off")
            return

        enabled = arg.lower() == "on"
        await runtime.queue.set_repeat(message.chat.id, enabled)
        await runtime.ui.refresh_now_playing(message.chat.id, force=True)
        await message.reply_text(f"Loop is now {'on' if enabled else 'off'}.")

    @app.on_message(filters.command(["volume"]) & filters.group)
    async def volume_handler(client: Client, message: Message) -> None:
        if not await _require_admin(client, message):
            return

        arg = command_arg(message)
        if not arg or not arg.isdigit():
            current = await runtime.queue.get_volume(message.chat.id)
            await message.reply_text(
                f"Current volume: {current}. Usage: /volume <10-200>"
            )
            return

        try:
            value = await runtime.voice.set_volume(message.chat.id, int(arg))
            await runtime.ui.refresh_now_playing(message.chat.id, force=True)
            await message.reply_text(f"Volume set to {value}")
        except Exception as exc:
            await runtime.log_event(f"/volume failed in {message.chat.id}: {exc}")
            await message.reply_text(f"Volume update failed: {exc}")

    @app.on_callback_query(filters.regex(r"^player:"))
    async def player_controls_handler(client: Client, query: CallbackQuery) -> None:
        if not query.message:
            await _safe_answer_query(query, "Control target was not found.", show_alert=True)
            return
        if not await _require_admin_query(client, query):
            return

        chat_id = query.message.chat.id
        action = (query.data or "").split(":", maxsplit=1)[-1]

        try:
            if action == "pause":
                await runtime.voice.pause(chat_id)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, "Playback paused")
            elif action == "skip":
                next_track = await runtime.voice.skip(chat_id)
                if next_track:
                    await runtime.ui.show_now_playing(chat_id, next_track)
                    await _safe_answer_query(query, "Skipped")
                else:
                    await runtime.ui.clear_now_playing(chat_id)
                    await _safe_answer_query(query, "Queue is now empty")
            elif action == "stop":
                await runtime.voice.stop(chat_id)
                await runtime.ui.clear_now_playing(chat_id)
                await _safe_answer_query(query, "Playback stopped")
            elif action == "loop":
                enabled = await runtime.queue.get_repeat(chat_id)
                await runtime.queue.set_repeat(chat_id, not enabled)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, f"Loop {'on' if not enabled else 'off'}")
            elif action == "voldown":
                current = await runtime.queue.get_volume(chat_id)
                value = await runtime.voice.set_volume(chat_id, current - 10)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, f"Volume: {value}%")
            elif action == "volup":
                current = await runtime.queue.get_volume(chat_id)
                value = await runtime.voice.set_volume(chat_id, current + 10)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, f"Volume: {value}%")
            else:
                await _safe_answer_query(query, "Unknown control", show_alert=True)
        except Exception as exc:
            await runtime.log_event(f"inline control failed in {chat_id}: {exc}")
            await _safe_answer_query(query, "Action failed", show_alert=True)

    @app.on_message(filters.command(["playlist_save"]) & filters.group)
    async def playlist_save_handler(client: Client, message: Message) -> None:
        name = command_arg(message)
        if not name:
            await message.reply_text("Usage: /playlist_save <name>")
            return

        current = await runtime.queue.get_current(message.chat.id)
        queue = await runtime.queue.list_queue(message.chat.id)
        tracks: list[Track] = ([current] if current else []) + queue
        if not tracks:
            await message.reply_text("No tracks in this chat queue to save.")
            return

        owner_id = message.from_user.id if message.from_user else 0
        await runtime.db.save_playlist(owner_id, name, tracks)
        await message.reply_text(f"Playlist '{name}' saved with {len(tracks)} tracks.")

    @app.on_message(filters.command(["playlist_load"]) & filters.group)
    async def playlist_load_handler(client: Client, message: Message) -> None:
        name = command_arg(message)
        if not name:
            await message.reply_text("Usage: /playlist_load <name>")
            return

        owner_id = message.from_user.id if message.from_user else 0
        rows = await runtime.db.load_playlist(owner_id, name)
        if not rows:
            await message.reply_text("Playlist not found or empty.")
            return

        for row in rows:
            await runtime.queue.enqueue(
                message.chat.id,
                Track(
                    title=row["title"],
                    source_url=row.get("source_url", row.get("webpage_url", "")),
                    webpage_url=row["webpage_url"],
                    duration=row.get("duration"),
                    requester_id=owner_id,
                    requester_name=_display_name(message),
                    thumbnail=row.get("thumbnail"),
                    stream_url=row.get("stream_url"),
                ),
            )
        current = await runtime.queue.get_current(message.chat.id)
        if not current:
            await runtime.voice.play_next(message.chat.id)

        await message.reply_text(f"Loaded {len(rows)} tracks into queue.")
