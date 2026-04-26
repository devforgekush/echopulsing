from __future__ import annotations

from typing import TYPE_CHECKING

from pyrogram import Client
from pyrogram.handlers import InlineQueryHandler
from pyrogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from echopulsing.utils.helpers import format_seconds

if TYPE_CHECKING:
    from echopulsing.services.runtime import Runtime


async def _inline_query_handler(client: Client, inline_query: InlineQuery, runtime: Runtime) -> None:
    query = (inline_query.query or "").strip()
    if not query:
        return

    try:
        rows = await runtime.ytdlp.search(query, limit=8)
    except Exception:
        return

    results = []
    for row in rows:
        video_id = row.get("id")
        title = row.get("title", "Unknown")
        duration = format_seconds(row.get("duration"))
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else query
        results.append(
            InlineQueryResultArticle(
                title=title,
                description=f"Duration: {duration}",
                input_message_content=InputTextMessageContent(f"/play {url}"),
            )
        )

    if results:
        await inline_query.answer(results=results, cache_time=0)


def register(app: Client, runtime: Runtime) -> None:
    app.add_handler(InlineQueryHandler(lambda c, q: _inline_query_handler(c, q, runtime)))
