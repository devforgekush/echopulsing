from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any

from pyrogram import Client, enums


@dataclass(slots=True)
class PendingPlayRequest:
    token: str
    chat_id: int
    requester_id: int
    requester_name: str
    query: str
    invite_link: str | None
    created_at: float


class AssistantService:
    _INVITE_LINK_TTL_SECONDS = 5 * 60
    _PENDING_REQUEST_TTL_SECONDS = 20 * 60
    _JOIN_COOLDOWN_SECONDS = 10

    def __init__(self, bot: Client, user: Client, logger: Any) -> None:
        self._bot = bot
        self._user = user
        self._logger = logger
        self._assistant_id: int | None = None
        self._assistant_username: str | None = None
        self._assistant_name: str = "assistant"
        self._invite_cache: dict[int, tuple[float, str]] = {}
        self._pending: dict[str, PendingPlayRequest] = {}
        self._last_join_attempt: dict[int, float] = {}
        self._join_locks: dict[int, asyncio.Lock] = {}
        self._assistant_present: dict[int, bool] = {}

    async def initialize(self) -> None:
        if self._assistant_id is not None:
            return
        me = await self._user.get_me()
        self._assistant_id = int(me.id)
        self._assistant_username = me.username
        self._assistant_name = (me.first_name or "assistant").strip() or "assistant"

    @property
    def assistant_label(self) -> str:
        if self._assistant_username:
            return f"@{self._assistant_username}"
        return self._assistant_name

    def _cleanup_expired(self) -> None:
        now = time.monotonic()

        stale_invites = [
            chat_id
            for chat_id, (created_at, _) in self._invite_cache.items()
            if (now - created_at) > self._INVITE_LINK_TTL_SECONDS
        ]
        for chat_id in stale_invites:
            self._invite_cache.pop(chat_id, None)

        stale_pending = [
            token
            for token, payload in self._pending.items()
            if (now - payload.created_at) > self._PENDING_REQUEST_TTL_SECONDS
        ]
        for token in stale_pending:
            self._pending.pop(token, None)

    async def is_in_chat(self, chat_id: int) -> bool:
        await self.initialize()
        self._cleanup_expired()
        if self._assistant_id is None:
            return False

        try:
            member = await self._bot.get_chat_member(chat_id, self._assistant_id)
            status = getattr(member, "status", None)
            return status not in {
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.BANNED,
            }
        except Exception as exc:
            text = str(exc).upper()
            if "USER_NOT_PARTICIPANT" in text or "PARTICIPANT_ID_INVALID" in text:
                return False
            self._logger.warning("Failed to check assistant membership in %s: %s", chat_id, exc)
            return False

    @staticmethod
    def _invite_error_message(exc: Exception) -> str:
        text = str(exc).upper()
        if "INVITE_HASH_EXPIRED" in text or "INVITE_HASH_INVALID" in text:
            return "Invite link is expired. Generate a new invite and try again."
        if "CHAT_ADMIN_REQUIRED" in text or "CHANNEL_PRIVATE" in text:
            return "This group does not allow invite-link export for the bot account."
        if "FLOOD_WAIT" in text or "PEER_FLOOD" in text:
            return "Invite operation is rate limited. Please wait and retry."
        return "Could not create invite link for this group."

    async def get_invite_link(self, chat_id: int) -> tuple[str | None, str | None]:
        self._cleanup_expired()
        cached = self._invite_cache.get(chat_id)
        if cached:
            return cached[1], None

        try:
            invite = await self._bot.create_chat_invite_link(chat_id)
            link = invite.invite_link
        except Exception as exc:
            return None, self._invite_error_message(exc)

        self._invite_cache[chat_id] = (time.monotonic(), link)
        return link, None

    def create_pending_play(
        self,
        *,
        chat_id: int,
        requester_id: int,
        requester_name: str,
        query: str,
        invite_link: str | None,
    ) -> PendingPlayRequest:
        self._cleanup_expired()
        token = secrets.token_hex(6)
        payload = PendingPlayRequest(
            token=token,
            chat_id=chat_id,
            requester_id=requester_id,
            requester_name=requester_name,
            query=query,
            invite_link=invite_link,
            created_at=time.monotonic(),
        )
        self._pending[token] = payload
        return payload

    def get_pending_play(self, token: str) -> PendingPlayRequest | None:
        self._cleanup_expired()
        return self._pending.get(token)

    def clear_pending_play(self, token: str) -> None:
        self._pending.pop(token, None)

    @staticmethod
    def _join_error_message(exc: Exception) -> str:
        text = str(exc).upper()
        if "INVITE_HASH_EXPIRED" in text or "INVITE_HASH_INVALID" in text:
            return "Invite link is invalid or expired."
        if "CHANNEL_PRIVATE" in text or "CHAT_ADMIN_REQUIRED" in text:
            return "Private group restrictions blocked assistant join."
        if "FLOOD_WAIT" in text or "PEER_FLOOD" in text:
            return "Join is rate limited. Please wait a bit, then press Retry."
        if "USER_BANNED_IN_CHANNEL" in text:
            return "Assistant is banned in this group. Unban it and retry."
        if "CHANNELS_TOO_MUCH" in text:
            return "Assistant reached the group limit. Leave unused groups and retry."
        return "Assistant could not join from invite link."

    async def try_join_with_invite(self, chat_id: int, invite_link: str) -> tuple[bool, str | None]:
        await self.initialize()
        try:
            await asyncio.wait_for(
                self._user.join_chat(invite_link),
                timeout=10,
            )
        except TimeoutError:
            return False, "Assistant join timed out. Please try again."
        except Exception as exc:
            text = str(exc).upper()
            if "USER_ALREADY_PARTICIPANT" not in text:
                return False, self._join_error_message(exc)

        if await self.is_in_chat(chat_id):
            return True, None
        return False, "Assistant is still not in this group."

    def _is_join_on_cooldown(self, chat_id: int) -> bool:
        """Check if a join attempt was recently made for this chat."""
        now = time.monotonic()
        last_attempt = self._last_join_attempt.get(chat_id, 0)
        return (now - last_attempt) < self._JOIN_COOLDOWN_SECONDS

    def _mark_join_attempted(self, chat_id: int) -> None:
        """Mark that a join attempt was made for this chat."""
        self._last_join_attempt[chat_id] = time.monotonic()

    def _get_join_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._join_locks:
            self._join_locks[chat_id] = asyncio.Lock()
        return self._join_locks[chat_id]

    def _set_assistant_presence(self, chat_id: int, present: bool) -> None:
        if present:
            self._assistant_present[chat_id] = True
        else:
            self._assistant_present.pop(chat_id, None)

    async def ensure_assistant_joins(self, chat_id: int) -> tuple[bool, str | None]:
        """
        Automatically ensure the assistant joins the chat.
        
        Returns:
            (success: bool, error_message: str | None)
            - (True, None) if already in chat or successfully joined
            - (False, error_msg) if join failed or cooldown active
        """
        lock = self._get_join_lock(chat_id)
        async with lock:
            await self.initialize()

            if self._assistant_present.get(chat_id):
                return True, None

            # Check if already in chat
            if await self.is_in_chat(chat_id):
                self._set_assistant_presence(chat_id, True)
                return True, None

            self._set_assistant_presence(chat_id, False)

            # Check cooldown to prevent spam
            if self._is_join_on_cooldown(chat_id):
                return False, "❌ Assistant join cooldown active (10s). Please wait."

            # Mark this attempt
            self._mark_join_attempted(chat_id)

            # Get invite link
            invite_link, invite_error = await self.get_invite_link(chat_id)
            if not invite_link:
                error_msg = invite_error or "❌ Bot must be admin to invite assistant"
                return False, error_msg

            # Try to join with invite link
            success, join_error = await self.try_join_with_invite(chat_id, invite_link)
            if not success:
                error_msg = join_error or "❌ Assistant failed to join"
                return False, error_msg

            # Verify join was successful
            await asyncio.sleep(1.5)

            if await self.is_in_chat(chat_id):
                self._set_assistant_presence(chat_id, True)
                return True, None

            self._set_assistant_presence(chat_id, False)
            return False, "❌ Assistant join verification failed"
