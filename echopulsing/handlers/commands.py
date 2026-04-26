from __future__ import annotations

import html
import os
import sys
import time
from typing import TYPE_CHECKING

from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from echopulsing.services.models import Track
from echopulsing.utils.helpers import command_arg, format_seconds, is_admin, is_authorized

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


def _assistant_missing_text(label: str, error: str | None = None) -> str:
    lines = [
        "⚠️ Assistant account is not in this group.",
        "👉 Click below to add assistant, then press Retry.",
        f"Assistant: {label}",
    ]
    if error:
        lines.append(f"\nReason: {error}")
    return "\n".join(lines)


def _assistant_keyboard(invite_link: str | None, token: str) -> InlineKeyboardMarkup:
    rows = []
    if invite_link:
        rows.append([InlineKeyboardButton("➕ Add Assistant", url=invite_link)])
    rows.append([InlineKeyboardButton("🔁 Retry", callback_data=f"assistant_retry:{token}")])
    return InlineKeyboardMarkup(rows)


async def _execute_playback(
    runtime: Runtime,
    message: Message,
    *,
    query: str,
    requester_id: int,
    requester_name: str,
) -> None:
    status_message = await runtime.ui.loading_animation(message)
    try:
        result = await runtime.playback.play_query(
            chat_id=message.chat.id,
            query=query,
            requester_id=requester_id,
            requester_name=requester_name,
        )

        if result.state == "playing":
            await runtime.ui.show_now_playing(message.chat.id, result.track)
        else:
            await message.reply_text(
                f"🎶 Queued at position #{result.position}: {_escape(result.track.title)}",
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


async def _execute_force_playback(
    runtime: Runtime,
    message: Message,
    *,
    query: str,
    requester_id: int,
    requester_name: str,
) -> None:
    status_message = await runtime.ui.loading_animation(message)
    try:
        track = await runtime.ytdlp.resolve(query, requester_id, requester_name)
        started = await runtime.voice.force_play(message.chat.id, track)
        if started is not None:
            await runtime.ui.show_now_playing(message.chat.id, started)
            await runtime.ui.refresh_now_playing(message.chat.id, force=True)
            await message.reply_text(
                f"⏩ Force playing: {_escape(started.title)}",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        await message.reply_text("❌ Failed to start forced playback")
    except ValueError as exc:
        if _is_no_results_error(exc):
            await message.reply_text("⚠️ No results found")
        else:
            await message.reply_text("❌ Failed to play track")
        await runtime.log_event(f"/playforce failed in {message.chat.id}: {exc}")
    except RuntimeError as exc:
        await message.reply_text(str(exc))
        await runtime.log_event(f"/playforce failed in {message.chat.id}: {exc}")
    except Exception as exc:
        await message.reply_text("❌ Failed to play track")
        await runtime.log_event(f"/playforce failed in {message.chat.id}: {exc}")
    finally:
        if status_message is not None:
            try:
                await status_message.delete()
            except Exception:
                pass


async def _prompt_assistant_join(
    runtime: Runtime,
    message: Message,
    *,
    query: str,
    requester_id: int,
    requester_name: str,
    extra_error: str | None = None,
) -> None:
    invite_link, invite_error = await runtime.assistant.get_invite_link(message.chat.id)
    reason = extra_error or invite_error
    pending = runtime.assistant.create_pending_play(
        chat_id=message.chat.id,
        requester_id=requester_id,
        requester_name=requester_name,
        query=query,
        invite_link=invite_link,
    )
    await message.reply_text(
        _assistant_missing_text(runtime.assistant.assistant_label, reason),
        reply_markup=_assistant_keyboard(invite_link, pending.token),
    )


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
    @app.on_message(filters.command(["restart"]))
    async def restart_handler(client: Client, message: Message) -> None:
        if not message.from_user:
            await message.reply_text("❌ Not allowed")
            return

        user_id = message.from_user.id
        allowed = is_authorized(user_id)
        allow_group_admin_restart = os.getenv("ALLOW_GROUP_ADMIN_RESTART", "").strip().lower() == "true"
        if (
            not allowed
            and allow_group_admin_restart
            and message.chat
            and message.chat.type in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}
        ):
            try:
                allowed = await is_admin(client, message.chat.id, user_id)
            except Exception:
                allowed = False

        if not allowed:
            await runtime.log_event(f"Unauthorized /restart by {user_id} in chat {message.chat.id}")
            await message.reply_text("❌ Not allowed")
            return

        await runtime.log_event(f"Authorized /restart by {user_id} in chat {message.chat.id}")
        await message.reply_text("♻️ Restarting bot...")
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            await runtime.log_event(f"/restart failed in chat {message.chat.id}: {exc}")
            await message.reply_text("❌ Restart failed")

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
        if not await runtime.assistant.is_in_chat(message.chat.id):
            await _prompt_assistant_join(
                runtime,
                message,
                query=query,
                requester_id=requester_id,
                requester_name=requester_name,
            )
            return

        await _execute_playback(
            runtime,
            message,
            query=query,
            requester_id=requester_id,
            requester_name=requester_name,
        )

    @app.on_message(filters.command(["playforce"]) & filters.group)
    async def playforce_handler(client: Client, message: Message) -> None:
        if not runtime.voice_available:
            await message.reply_text(
                "Voice backend is unavailable on this host. Run the bot in Linux (Docker/WSL/VPS)."
            )
            return

        query = command_arg(message)
        if not query:
            await message.reply_text("Usage: /playforce <song name or YouTube URL>")
            return

        requester_id = message.from_user.id if message.from_user else 0
        requester_name = _display_name(message)
        if not await runtime.assistant.is_in_chat(message.chat.id):
            await _prompt_assistant_join(
                runtime,
                message,
                query=query,
                requester_id=requester_id,
                requester_name=requester_name,
                extra_error="Assistant must join before /playforce can start audio.",
            )
            return

        await _execute_force_playback(
            runtime,
            message,
            query=query,
            requester_id=requester_id,
            requester_name=requester_name,
        )

    @app.on_callback_query(filters.regex(r"^assistant_retry:"))
    async def assistant_retry_handler(client: Client, query: CallbackQuery) -> None:
        if not query.message or not query.from_user:
            await _safe_answer_query(query, "Play request not found.", show_alert=True)
            return

        token = (query.data or "").split(":", maxsplit=1)[-1]
        pending = runtime.assistant.get_pending_play(token)
        if not pending:
            await _safe_answer_query(query, "Retry request expired. Run /play again.", show_alert=True)
            return

        if pending.chat_id != query.message.chat.id:
            await _safe_answer_query(query, "This retry button belongs to another chat.", show_alert=True)
            return

        is_owner = query.from_user.id == pending.requester_id
        is_chat_admin = await is_admin(client, pending.chat_id, query.from_user.id)
        if not is_owner and not is_chat_admin:
            await _safe_answer_query(query, "Only requester or admins can retry.", show_alert=True)
            return

        await _safe_answer_query(query, "Checking assistant...")

        join_error: str | None = None
        if not await runtime.assistant.is_in_chat(pending.chat_id):
            if pending.invite_link:
                _, join_error = await runtime.assistant.try_join_with_invite(
                    pending.chat_id,
                    pending.invite_link,
                )
            else:
                join_error = "No invite link available for auto-join in this group."

        if not await runtime.assistant.is_in_chat(pending.chat_id):
            invite_link, invite_error = await runtime.assistant.get_invite_link(pending.chat_id)
            refreshed = runtime.assistant.create_pending_play(
                chat_id=pending.chat_id,
                requester_id=pending.requester_id,
                requester_name=pending.requester_name,
                query=pending.query,
                invite_link=invite_link,
            )
            reason = join_error or invite_error
            await query.message.edit_text(
                _assistant_missing_text(runtime.assistant.assistant_label, reason),
                reply_markup=_assistant_keyboard(invite_link, refreshed.token),
            )
            runtime.assistant.clear_pending_play(token)
            return

        runtime.assistant.clear_pending_play(token)
        await query.message.edit_text("✅ Assistant joined. Retrying playback...")
        await _execute_playback(
            runtime,
            query.message,
            query=pending.query,
            requester_id=pending.requester_id,
            requester_name=pending.requester_name,
        )

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
        if not arg:
            mode = await runtime.queue.get_loop_mode(message.chat.id)
            await message.reply_text("Loop mode: {}. Use /loop off|single|all".format(mode))
            return

        normalized = arg.lower()
        if normalized == "on":
            normalized = "single"
        if normalized not in {"off", "single", "all"}:
            await message.reply_text("Usage: /loop off|single|all")
            return

        mode = await runtime.queue.set_loop_mode(message.chat.id, normalized)
        await runtime.ui.refresh_now_playing(message.chat.id, force=True)
        await message.reply_text(f"Loop mode is now {mode}.")

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
            if action == "toggle":
                paused = await runtime.voice.toggle_pause(chat_id)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, "Paused" if paused else "Playing")
            elif action == "skip":
                next_track = await runtime.voice.skip(chat_id)
                if next_track:
                    await runtime.ui.show_now_playing(chat_id, next_track)
                    await _safe_answer_query(query, "Skipped")
                else:
                    await runtime.ui.clear_now_playing(chat_id)
                    await _safe_answer_query(query, "Queue is now empty")
            elif action == "loop":
                mode = await runtime.queue.cycle_loop_mode(chat_id)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                await _safe_answer_query(query, f"Loop mode: {mode}")
            elif action == "shuffle":
                count = await runtime.queue.shuffle(chat_id)
                await runtime.voice.invalidate_prefetch(chat_id)
                await runtime.ui.refresh_now_playing(chat_id, force=True)
                if count:
                    await _safe_answer_query(query, f"Shuffled {count} queued tracks")
                else:
                    await _safe_answer_query(query, "Queue is empty")
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
