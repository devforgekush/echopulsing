from __future__ import annotations

import os

from pyrogram.client import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import Message


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
