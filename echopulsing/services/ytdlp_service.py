from __future__ import annotations

import asyncio
import os
import shutil
import time
import tempfile
from pathlib import Path
from dataclasses import replace
from typing import Any

from yt_dlp import YoutubeDL

from echopulsing.services.models import Track


class YtDlpService:
    _CACHE_TTL_SECONDS = 15 * 60

    def __init__(
        self,
        temp_dir: str,
        concurrency: int = 2,
        cookies_file: str | None = None,
        ffmpeg_location: str | None = None,
    ) -> None:
        self._temp_dir = temp_dir
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._cookies_file = self._detect_cookies_file(cookies_file)
        self._ffmpeg_location = ffmpeg_location or self._detect_ffmpeg_location()
        self._track_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        os.makedirs(self._temp_dir, exist_ok=True)

    def _detect_ffmpeg_location(self) -> str | None:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return os.path.dirname(ffmpeg_path)

        try:
            import imageio_ffmpeg  # type: ignore

            bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            if bundled_ffmpeg:
                return os.path.dirname(bundled_ffmpeg)
        except Exception:
            return None

        return None

    def _detect_cookies_file(self, cookies_file: str | None) -> str | None:
        if cookies_file:
            path = Path(cookies_file)
            return str(path) if path.exists() else None

        default_path = Path("cookies.txt")
        return str(default_path) if default_path.exists() else None

    @staticmethod
    def _cache_keys(query_or_url: str, data: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        normalized = query_or_url.strip().lower()
        if normalized:
            keys.append(normalized)
        webpage_url = (data.get("webpage_url") or "").strip().lower()
        if webpage_url and webpage_url not in keys:
            keys.append(webpage_url)
        video_id = (data.get("id") or "").strip().lower()
        if video_id and video_id not in keys:
            keys.append(video_id)
        return keys

    def _get_cached_payload(self, query_or_url: str) -> dict[str, Any] | None:
        now = time.monotonic()
        key = query_or_url.strip().lower()
        cached = self._track_cache.get(key)
        if not cached:
            return None
        created_at, payload = cached
        if (now - created_at) > self._CACHE_TTL_SECONDS:
            self._track_cache.pop(key, None)
            return None
        return payload

    def _store_cached_payload(self, query_or_url: str, payload: dict[str, Any]) -> None:
        created_at = time.monotonic()
        for key in self._cache_keys(query_or_url, payload):
            self._track_cache[key] = (created_at, dict(payload))

    @staticmethod
    def _is_direct_stream_url(value: str | None) -> bool:
        if not value:
            return False
        lowered = value.lower()
        if not lowered.startswith(("http://", "https://")):
            return False
        return "youtube.com/watch" not in lowered and "youtu.be/" not in lowered

    @staticmethod
    def _friendly_error(exc: Exception) -> Exception:
        message = str(exc).lower()
        if "age" in message and "restrict" in message:
            return ValueError("This video is age-restricted. Add cookies.txt or try a different track.")
        if "private video" in message or "video unavailable" in message or "not available" in message:
            return ValueError("This video is unavailable right now.")
        if "invalid url" in message or "unsupported url" in message or "no results found" in message:
            return ValueError("Invalid YouTube link or search query.")
        return exc

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "default_search": f"ytsearch{limit}",
        }
        if self._cookies_file:
            ydl_opts["cookiefile"] = self._cookies_file
        if self._ffmpeg_location:
            ydl_opts["ffmpeg_location"] = self._ffmpeg_location

        def _run() -> list[dict[str, Any]]:
            with YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(query, download=False)
                return data.get("entries", []) if data else []

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:
            raise self._friendly_error(exc) from exc

    async def resolve(self, query_or_url: str, requester_id: int, requester_name: str) -> Track:
        cached = self._get_cached_payload(query_or_url)
        if cached is not None:
            return Track(
                id=cached.get("id"),
                title=cached.get("title") or "Unknown title",
                source_url=cached.get("source_url") or cached.get("webpage_url") or query_or_url,
                webpage_url=cached.get("webpage_url") or cached.get("source_url") or query_or_url,
                duration=cached.get("duration"),
                requester_id=requester_id,
                requester_name=requester_name,
                thumbnail=cached.get("thumbnail"),
                stream_url=cached.get("stream_url"),
            )

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "default_search": "ytsearch1",
            "noplaylist": True,
            "format": "bestaudio/best",
            "geo_bypass": True,
            "nocheckcertificate": True,
            "no_warnings": True,
        }
        if self._cookies_file:
            ydl_opts["cookiefile"] = self._cookies_file
        if self._ffmpeg_location:
            ydl_opts["ffmpeg_location"] = self._ffmpeg_location

        def _run() -> dict[str, Any]:
            with YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(query_or_url, download=False)
                if "entries" in data and data["entries"]:
                    return data["entries"][0]
                return data

        try:
            data = await asyncio.to_thread(_run)
        except Exception as exc:
            raise self._friendly_error(exc) from exc

        if not data:
            raise ValueError("No results found")

        webpage_url = data.get("webpage_url") or data.get("url")
        if not webpage_url:
            raise ValueError("Could not resolve media URL")

        direct_url = data.get("url")
        if not self._is_direct_stream_url(direct_url):
            direct_url = None

        source_url = webpage_url
        payload = {
            "id": data.get("id"),
            "title": data.get("title") or "Unknown title",
            "source_url": source_url,
            "webpage_url": webpage_url,
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "stream_url": direct_url,
        }
        self._store_cached_payload(query_or_url, payload)

        return Track(
            id=payload["id"],
            title=payload["title"],
            source_url=source_url,
            webpage_url=webpage_url,
            duration=payload["duration"],
            requester_id=requester_id,
            requester_name=requester_name,
            thumbnail=payload["thumbnail"],
            stream_url=payload["stream_url"],
        )

    async def download_audio(self, track: Track) -> Track:
        async with self._sem:
            target_dir = tempfile.mkdtemp(prefix="music_", dir=self._temp_dir)
            outtmpl = os.path.join(target_dir, "%(id)s.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "quiet": True,
                "noplaylist": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            if self._cookies_file:
                ydl_opts["cookiefile"] = self._cookies_file
            if self._ffmpeg_location:
                ydl_opts["ffmpeg_location"] = self._ffmpeg_location

            def _run() -> str:
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([track.webpage_url])
                for name in os.listdir(target_dir):
                    if name.endswith(".mp3"):
                        return os.path.join(target_dir, name)
                raise RuntimeError("Audio file not produced by yt-dlp")

            try:
                track.file_path = await asyncio.to_thread(_run)
                return track
            except Exception as exc:
                self.cleanup_directory(target_dir)
                raise self._friendly_error(exc) from exc

    def cleanup_directory(self, dir_path: str) -> None:
        if os.path.isdir(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)

    async def cleanup_track_file(self, track: Track | None) -> None:
        if not track or not track.file_path:
            return
        file_path = track.file_path
        dir_path = os.path.dirname(file_path)

        def _cleanup() -> None:
            if os.path.isfile(file_path):
                os.remove(file_path)
            self.cleanup_directory(dir_path)

        await asyncio.to_thread(_cleanup)
