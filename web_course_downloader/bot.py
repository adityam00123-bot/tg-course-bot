"""
Main Entrypoint for Web & Classplus Course Downloader Telegram Bot.
"""

import sys
import logging
import asyncio
from pyrogram import Client, idle

from config import Config
from handlers import register_bot_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-7s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("web_course_downloader")


async def main():
    logger.info("=================================================================")
    logger.info("   >> COURSEVERSE WEB & CLASSPLUS COURSE DOWNLOADER BOT <<      ")
    logger.info("=================================================================")
    logger.info(f" 📌 Owner ID:     {Config.OWNER_ID}")
    logger.info(f" 📌 Download Dir: {Config.DOWNLOAD_DIR}")
    logger.info("=================================================================")

    # Initialize Bot Client
    app = Client(
        name="web_course_downloader_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        in_memory=True
    )

    # Register handlers
    register_bot_handlers(app)

    # Start Bot
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Bot Online & Ready: @{me.username} (ID: {me.id})")
    logger.info("🚀 Listening for user commands (/start, /login, stream links)...")

    # Run until interrupted
    await idle()
    await app.stop()
    logger.info("🛑 Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
