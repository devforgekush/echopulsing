from __future__ import annotations

import asyncio
import heapq
import os
import shutil
import time
from pathlib import Path
from dataclasses import replace
from typing import Any

from yt_dlp import YoutubeDL

from echopulsing.services.models import Track


class YtDlpService:
    _CACHE_TTL_SECONDS = 30 * 60
    _MAX_CACHE_SIZE = 500
    _PRUNE_BATCH_SIZE = 50
    _EXTRACT_RETRIES = 3

    def __init__(
        self,
        cookies_file: str | None = None,
        ffmpeg_location: str | None = None,
    ) -> None:
        self._cookies_file = self._detect_cookies_file(cookies_file)
        self._ffmpeg_location = ffmpeg_location or self._detect_ffmpeg_location()
        self._track_cache: dict[str, tuple[float, dict[str, Any]]] = {}

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
        self._prune_cache()

    def _prune_cache(self) -> None:
        if len(self._track_cache) <= self._MAX_CACHE_SIZE:
            return

        now = time.monotonic()
        expired_keys = [
            key
            for key, (created_at, _) in self._track_cache.items()
            if (now - created_at) > self._CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            self._track_cache.pop(key, None)

        if len(self._track_cache) <= self._MAX_CACHE_SIZE:
            return

        overflow = len(self._track_cache) - self._MAX_CACHE_SIZE
        trim_count = max(overflow, self._PRUNE_BATCH_SIZE)
        oldest_items = heapq.nsmallest(
            trim_count,
            self._track_cache.items(),
            key=lambda item: item[1][0],
        )
        for key, _ in oldest_items:
            self._track_cache.pop(key, None)

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

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        retryable_markers = (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "temporary failure",
            "connection reset",
            "connection aborted",
            "network error",
            "http error 429",
            "http error 503",
            "too many requests",
            "remote end closed connection",
            "unable to download",
            "transport error",
        )
        return any(marker in message for marker in retryable_markers)

    @staticmethod
    def _is_cookie_related_error(exc: Exception) -> bool:
        message = str(exc).lower()
        cookie_markers = (
            "sign in to confirm your age",
            "age-restricted",
            "confirm your age",
            "cookie",
            "login required",
            "for this video",
        )
        return any(marker in message for marker in cookie_markers)

    @staticmethod
    def _is_invalid_cookie_error(exc: Exception) -> bool:
        message = str(exc).lower()
        invalid_markers = (
            "cookie file",
            "cookies",
            "cookiejar",
            "expired",
            "invalid cookie",
            "invalid cookies",
        )
        return any(marker in message for marker in invalid_markers)

    @staticmethod
    def _first_entry(data: dict[str, Any] | None) -> dict[str, Any] | None:
        if not data:
            return None
        if isinstance(data, dict) and data.get("entries"):
            return data["entries"][0]
        return data

    async def _extract_info(
        self,
        target: str,
        *,
        default_search: str | None = None,
        use_cookies: bool = False,
        extract_flat: bool = False,
    ) -> dict[str, Any]:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "geo_bypass": True,
            "nocheckcertificate": True,
            "no_warnings": True,
        }
        if default_search:
            ydl_opts["default_search"] = default_search
        if extract_flat:
            ydl_opts["extract_flat"] = "in_playlist"
        if use_cookies and self._cookies_file:
            ydl_opts["cookiefile"] = self._cookies_file
        if self._ffmpeg_location:
            ydl_opts["ffmpeg_location"] = self._ffmpeg_location

        def _run() -> dict[str, Any]:
            with YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(target, download=False)
                return data or {}

        return await asyncio.to_thread(_run)

    async def _extract_with_fallback(
        self,
        target: str,
        *,
        default_search: str | None = None,
    ) -> dict[str, Any]:
        attempts = [False]
        if self._cookies_file:
            attempts.append(True)

        last_error: Exception | None = None

        for use_cookies in attempts:
            for attempt in range(self._EXTRACT_RETRIES):
                try:
                    return await self._extract_info(
                        target,
                        default_search=default_search,
                        use_cookies=use_cookies,
                    )
                except Exception as exc:
                    last_error = exc
                    if use_cookies and self._is_invalid_cookie_error(exc):
                        raise ValueError(
                            "cookies.txt is invalid or expired. Refresh it and retry."
                        ) from exc
                    if not self._is_retryable_error(exc) or attempt >= self._EXTRACT_RETRIES - 1:
                        break
                    await asyncio.sleep(0.25 * (attempt + 1))

        if last_error is not None:
            if not self._cookies_file and self._is_cookie_related_error(last_error):
                raise ValueError(
                    "This video requires cookies.txt. Set YTDLP_COOKIES_FILE and try again."
                ) from last_error
            raise self._friendly_error(last_error) from last_error

        raise RuntimeError("Could not resolve stream URL")

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            data = await self._extract_info(query, default_search=f"ytsearch{limit}", extract_flat=True)
            return data.get("entries", []) if data else []
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

        try:
            data = await self._extract_with_fallback(query_or_url, default_search="ytsearch1")
        except Exception as exc:
            raise self._friendly_error(exc) from exc

        data = self._first_entry(data)
        if not data:
            raise ValueError("No results found")

        webpage_url = data.get("webpage_url") or data.get("original_url") or query_or_url
        if not webpage_url:
            raise ValueError("Could not resolve media URL")

        direct_url = data.get("url")
        if not self._is_direct_stream_url(direct_url):
            raise ValueError("Could not resolve a direct stream URL")

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

    async def ensure_stream_url(self, track: Track) -> Track:
        if self._is_direct_stream_url(track.stream_url):
            return track

        lookup_keys = [track.webpage_url, track.source_url, track.id or ""]
        for key in lookup_keys:
            if not key:
                continue
            cached = self._get_cached_payload(key)
            if cached and self._is_direct_stream_url(cached.get("stream_url")):
                return replace(track, stream_url=cached.get("stream_url"))

        try:
            data = await self._extract_with_fallback(track.webpage_url)
        except Exception:
            return track

        data = self._first_entry(data)
        if not data:
            return track

        direct_url = data.get("url")
        if not self._is_direct_stream_url(direct_url):
            return track

        payload = {
            "id": data.get("id") or track.id,
            "title": data.get("title") or track.title,
            "source_url": data.get("webpage_url") or track.source_url,
            "webpage_url": data.get("webpage_url") or track.webpage_url,
            "duration": data.get("duration") or track.duration,
            "thumbnail": data.get("thumbnail") or track.thumbnail,
            "stream_url": direct_url,
        }
        self._store_cached_payload(track.webpage_url, payload)
        self._store_cached_payload(track.source_url, payload)

        return replace(track, stream_url=direct_url)

