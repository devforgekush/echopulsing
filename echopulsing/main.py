from __future__ import annotations

import asyncio

from pyrogram import idle

from echopulsing.config import Settings
from echopulsing.handlers.commands import register as register_commands
from echopulsing.handlers.inline import register as register_inline
from echopulsing.services.runtime import Runtime
from echopulsing.utils.logger import setup_logging
from echopulsing.utils.pyrogram_patch import apply_peer_id_patch


async def run() -> None:
    logger = setup_logging()
    apply_peer_id_patch()
    settings = Settings.from_env()
    runtime = Runtime(settings, logger)

    register_commands(runtime.bot, runtime)
    register_inline(runtime.bot, runtime)

    await runtime.start()
    logger.info("%s is running in long-polling mode", settings.bot_name)

    try:
        await idle()
    finally:
        await runtime.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
