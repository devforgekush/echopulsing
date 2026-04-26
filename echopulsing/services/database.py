from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from echopulsing.services.models import Track


class Database:
    def __init__(self, mongo_uri: str) -> None:
        self._client = AsyncIOMotorClient(mongo_uri)
        self._db: AsyncIOMotorDatabase = self._client["telegram_music_bot"]

    async def ping(self) -> None:
        await self._db.command("ping")

    async def close(self) -> None:
        self._client.close()

    async def save_playlist(self, owner_id: int, name: str, tracks: list[Track]) -> None:
        payload = {
            "owner_id": owner_id,
            "name": name.lower(),
            "tracks": [
                {
                    "title": t.title,
                    "source_url": t.source_url,
                    "webpage_url": t.webpage_url,
                    "duration": t.duration,
                    "thumbnail": t.thumbnail,
                    "stream_url": t.stream_url,
                }
                for t in tracks
            ],
        }
        await self._db.playlists.update_one(
            {"owner_id": owner_id, "name": name.lower()},
            {"$set": payload},
            upsert=True,
        )

    async def load_playlist(self, owner_id: int, name: str) -> list[dict[str, Any]]:
        data = await self._db.playlists.find_one({"owner_id": owner_id, "name": name.lower()})
        return data.get("tracks", []) if data else []

    async def delete_playlist(self, owner_id: int, name: str) -> bool:
        result = await self._db.playlists.delete_one({"owner_id": owner_id, "name": name.lower()})
        return result.deleted_count > 0

    async def list_playlists(self, owner_id: int) -> list[str]:
        cursor = self._db.playlists.find({"owner_id": owner_id}, {"name": 1, "_id": 0})
        return [doc["name"] async for doc in cursor]
