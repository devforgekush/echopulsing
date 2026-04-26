from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

BOT_NAME = "EchoPulsing Music"


@dataclass(slots=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    string_session: str
    mongo_uri: str
    log_channel_id: int | None
    ytdlp_cookies_file: str | None
    ffmpeg_location: str | None
    bot_name: str = BOT_NAME

    @classmethod
    def from_env(cls) -> "Settings":
        api_id = os.getenv("API_ID")
        api_hash = os.getenv("API_HASH")
        bot_token = os.getenv("BOT_TOKEN")
        string_session = os.getenv("STRING_SESSION")
        mongo_uri = os.getenv("MONGO_URI")

        missing = [
            name
            for name, value in [
                ("API_ID", api_id),
                ("API_HASH", api_hash),
                ("BOT_TOKEN", bot_token),
                ("STRING_SESSION", string_session),
                ("MONGO_URI", mongo_uri),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        log_channel_raw = os.getenv("LOG_CHANNEL_ID", "").strip()
        log_channel_id = int(log_channel_raw) if log_channel_raw else None

        cookies_path = os.getenv("YTDLP_COOKIES_FILE", "").strip() or None
        if cookies_path and not Path(cookies_path).exists():
            cookies_path = None
        if not cookies_path and Path("cookies.txt").exists():
            cookies_path = "cookies.txt"

        return cls(
            api_id=int(api_id),
            api_hash=api_hash or "",
            bot_token=bot_token or "",
            string_session=string_session or "",
            mongo_uri=mongo_uri or "",
            log_channel_id=log_channel_id,
            ytdlp_cookies_file=cookies_path,
            ffmpeg_location=os.getenv("FFMPEG_LOCATION") or None,
        )
