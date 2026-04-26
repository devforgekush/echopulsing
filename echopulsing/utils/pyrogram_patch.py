from __future__ import annotations

from pyrogram import utils as pyrogram_utils


def apply_peer_id_patch() -> None:
    original = pyrogram_utils.get_peer_type

    def patched_get_peer_type(peer_id: int | str) -> str:
        if isinstance(peer_id, str):
            try:
                peer_id = int(peer_id)
            except Exception:
                return original(peer_id)

        if peer_id < 0 and str(peer_id).startswith("-100"):
            return "channel"
        return original(peer_id)

    pyrogram_utils.get_peer_type = patched_get_peer_type
