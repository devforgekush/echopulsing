from __future__ import annotations

import os
import time

from pyrogram.client import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import Message


_PROCESS_STARTED_AT = time.monotonic()


def _parse_user_id(value: str | None) -> int | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    parsed: set[int] = set()
    for item in value.split(","):
        user_id = _parse_user_id(item)
        if user_id is not None:
            parsed.add(user_id)
    return parsed


def is_authorized(user_id: int) -> bool:
    owner_id = _parse_user_id(os.getenv("OWNER_ID"))
    admin_ids = _parse_admin_ids(os.getenv("ADMINS"))
    return user_id == owner_id or user_id in admin_ids


async def is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    member = await client.get_chat_member(chat_id, user_id)
    return member.status in {
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    }


def command_arg(message: Message) -> str | None:
    if not message.text:
        return None
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    value = parts[1].strip()
    return value or None


def format_seconds(total_seconds: int | None) -> str:
    if not total_seconds or total_seconds <= 0:
        return "live"
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def trim_title(title: str | None, max_length: int = 60) -> str:
    value = (title or "").strip()
    if not value:
        return "Unknown title"
    if max_length <= 3:
        return value[:max_length]
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."


def get_system_usage_percent() -> tuple[str, str]:
    try:
        import psutil  # type: ignore

        cpu = max(0.0, min(100.0, float(psutil.cpu_percent(interval=0.5))))
        ram = max(0.0, min(100.0, float(psutil.virtual_memory().percent)))
        return f"{cpu:.0f}%", f"{ram:.0f}%"
    except Exception:
        return "N/A", "N/A"


def get_uptime_seconds() -> int:
    return max(0, int(time.monotonic() - _PROCESS_STARTED_AT))


def format_uptime(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m {secs:02d}s"
    if hours:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes:02d}m {secs:02d}s"
    return f"{secs}s"
