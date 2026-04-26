from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from pyrogram import Client

try:
    from pytgcalls import PyTgCalls
except Exception as exc:  # pragma: no cover
    PyTgCalls = None  # type: ignore[assignment]
    _PYTGCALLS_IMPORT_ERROR = exc
else:
    _PYTGCALLS_IMPORT_ERROR = None

from echopulsing.config import Settings
from echopulsing.services.database import Database
from echopulsing.services.disabled_voice_service import DisabledVoiceService
from echopulsing.services.models import Track
from echopulsing.services.queue_manager import QueueManager
from echopulsing.services.ytdlp_service import YtDlpService
from echopulsing.utils.player_ui import PlayerUI

if TYPE_CHECKING:
    from echopulsing.services.voice_service import VoiceService


class Runtime:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.voice_available = True

        if settings.ffmpeg_location:
            current_path = os.environ.get("PATH", "")
            if settings.ffmpeg_location not in current_path.split(os.pathsep):
                os.environ["PATH"] = f"{settings.ffmpeg_location}{os.pathsep}{current_path}"

        self.bot = Client(
            "echopulsing-music-bot",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            bot_token=settings.bot_token,
            workdir=".",
            in_memory=False,
        )
        self.user = Client(
            "echopulsing-music-user",
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            session_string=settings.string_session,
            workdir=".",
            in_memory=False,
        )

        self.calls = PyTgCalls(self.user) if PyTgCalls is not None else None
        self.db = Database(settings.mongo_uri)
        self.queue = QueueManager()
        self.ui = PlayerUI(self.bot, self)
        self.ytdlp = YtDlpService(
            temp_dir=settings.temp_dir,
            concurrency=settings.download_concurrency,
            cookies_file=settings.ytdlp_cookies_file,
            ffmpeg_location=settings.ffmpeg_location,
        )
        if self.calls is None:
            self.voice_available = False
            self.voice = DisabledVoiceService(
                "Voice streaming backend is unavailable on this host. "
                "Use Linux (Docker/WSL/VPS) for voice chat streaming."
            )
        else:
            try:
                from echopulsing.services.voice_service import VoiceService

                self.voice: VoiceService = VoiceService(self.calls, self.queue, self.ytdlp, logger)
                self.voice.set_auto_transition_callback(self._sync_ui_after_auto_transition)
            except Exception as exc:
                self.voice_available = False
                self.logger.warning("Voice service disabled: %s", exc)
                self.voice = DisabledVoiceService(
                    "Voice streaming backend failed to initialize. "
                    "Use Linux (Docker/WSL/VPS) for voice chat streaming."
                )

    async def _sync_ui_after_auto_transition(self, chat_id: int, track: Track | None) -> None:
        if track is None:
            await self.ui.clear_now_playing(chat_id)
            return
        await self.ui.show_now_playing(chat_id, track)

    async def start(self) -> None:
        await self.db.ping()
        await self.bot.start()
        if self.voice_available and self.calls is not None:
            await self.user.start()
            await self.calls.start()
        else:
            self.logger.warning(
                "Started without voice backend. Bot commands are available, voice commands are disabled."
            )
        self.logger.info("Runtime started for %s", self.settings.bot_name)

    async def log_event(self, text: str) -> None:
        self.logger.info(text)
        if self.settings.log_channel_id is None:
            return
        self.logger.debug("LOG_CHANNEL_ID is configured, but chat delivery is disabled to avoid spam.")

    async def stop(self) -> None:
        try:
            if self.calls is not None and hasattr(self.calls, "stop"):
                await self.calls.stop()
        except Exception:
            pass
        if self.voice_available:
            await self.user.stop()
        await self.bot.stop()
        await self.db.close()
