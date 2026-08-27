"""
Headless Telegram Migration Runner for GitHub Actions / 24/7 Cloud Automation.
Runs non-interactively, auto-resumes from migration_progress.json, and gracefully
stops before GitHub Actions 6-hour job timeout to save checkpoint.
"""

import os
import sys
import time
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load env variables explicitly
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

from config import Config
from client import create_bot_client, get_or_create_user_client
from migration import MigrationEngine, MigrationConfig, EngineType, OutputFormat, load_checkpoint

# Configure logging to stdout & file
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-7s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("migration.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("headless_runner")

# Maximum execution time before clean exit (5 hours 20 minutes = 19200 seconds)
MAX_RUN_SECONDS = int(os.getenv("MAX_RUN_SECONDS", "19200"))


async def run_headless():
    start_time = time.time()
    logger.info("=" * 65)
    logger.info("  >> 24/7 GITHUB ACTIONS HEADLESS MIGRATION ENGINE <<  ")
    logger.info("=" * 65)

    source_chat_id = (os.getenv("SOURCE_CHAT_ID") or "").strip() or "-1001895806383"
    dest_chat_id = (os.getenv("DEST_CHAT_ID") or "").strip() or "-1004317253896"

    # Convert numeric chat IDs if applicable
    try:
        source_chat = int(source_chat_id)
    except ValueError:
        source_chat = source_chat_id

    try:
        dest_chat = int(dest_chat_id)
    except ValueError:
        dest_chat = dest_chat_id

    owner_id = Config.OWNER_ID or 8383627571

    logger.info(f"📌 Source Channel: {source_chat}")
    logger.info(f"📌 Destination:    {dest_chat}")
    logger.info(f"📌 Max Duration:   {MAX_RUN_SECONDS // 60} minutes")
    logger.info("=" * 65)

    # Initialize Userbot Client (via SESSION_STRING or session file)
    userbot = await get_or_create_user_client(owner_id)
    if not userbot:
        logger.error("❌ Failed to initialize Userbot! Please ensure SESSION_STRING is set in GitHub Secrets.")
        sys.exit(1)

    try:
        user_me = await userbot.get_me()
        user_name = getattr(user_me, "username", None) or getattr(user_me, "id", "User")
        logger.info(f"✅ Userbot Online: (ID: {user_me.id}, @{user_name})")
    except Exception:
        pass

    # Initialize Bot Client (with fallback to userbot if FloodWait or token error occurs)
    bot = None
    try:
        b_client = create_bot_client(in_memory=True)
        await b_client.start()
        bot_me = await b_client.get_me()
        logger.info(f"✅ Bot Online: @{bot_me.username} (ID: {bot_me.id})")
        bot = b_client
    except Exception as e:
        logger.warning(f"⚠️ Bot client warning: {e}. Falling back to userbot client for notifications.")
        bot = userbot

    from migration import MigrationMode, save_checkpoint

    # Check last checkpoint
    last_checkpoint = load_checkpoint(source_chat, dest_chat)

    # Support custom START_MSG_ID from environment
    start_msg_id_env = (os.getenv("START_MSG_ID") or "").strip()
    if start_msg_id_env and start_msg_id_env.isdigit():
        custom_start = int(start_msg_id_env)
        save_checkpoint(source_chat, dest_chat, custom_start - 1)
        last_checkpoint = custom_start - 1
    elif last_checkpoint == 0:
        save_checkpoint(source_chat, dest_chat, 265)
        last_checkpoint = 265

    logger.info(f"🔄 Current Checkpoint: Message #{last_checkpoint} (Starting from #{last_checkpoint + 1})")

    thumb_file = Config.BASE_DIR / "thumb.jpg"
    has_custom_thumb = thumb_file.exists()
    if has_custom_thumb:
        logger.info(f"🖼️ Custom Thumbnail Found: {thumb_file}")

    # Build Migration Configuration
    mig_config = MigrationConfig(
        engine_type=EngineType.USERBOT,
        source_chat_id=source_chat,
        source_chat_title=f"Source [{source_chat}]",
        dest_chat_id=dest_chat,
        dest_chat_title=f"Dest [{dest_chat}]",
        mode=MigrationMode.FULL,
        start_msg_id=last_checkpoint + 1,
        output_format=OutputFormat.VIDEO,
        enable_watermark=False,
        enable_custom_thumbnail=has_custom_thumb,
        custom_thumbnail_path=str(thumb_file) if has_custom_thumb else None
    )

    engine = MigrationEngine(
        userbot=userbot,
        bot=bot,
        owner_id=owner_id
    )
    engine.config = mig_config

    # Launch migration job
    logger.info("🚀 Launching Streaming Migration Pipeline...")
    await engine.start_job()

    # Monitor runtime loop
    try:
        while engine.is_busy():
            elapsed = time.time() - start_time
            if elapsed >= MAX_RUN_SECONDS:
                logger.info(f"⌛ Max run time limit reached ({MAX_RUN_SECONDS // 60}m). Stopping gracefully to save checkpoint...")
                engine.cancel_job()
                break
            await asyncio.sleep(5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Cancel signal received. Stopping gracefully...")
        engine.cancel_job()

    # Await clean termination
    if engine._running_task and not engine._running_task.done():
        try:
            await asyncio.wait_for(engine._running_task, timeout=15)
        except (asyncio.CancelledError, Exception):
            pass

    # Save final logs & shutdown clients
    final_checkpoint = load_checkpoint(source_chat, dest_chat)
    logger.info("=" * 65)
    logger.info(f"🎉 Session Complete! Progress saved at Message #{final_checkpoint}")
    logger.info(f"📊 Total Migrated in this run: {engine.stats.media_count} media items ({engine.stats.total_bytes_migrated / 1048576:.1f} MB)")
    logger.info("=" * 65)

    try:
        if bot and bot != userbot:
            await bot.stop()
    except Exception:
        pass
    try:
        if userbot:
            await userbot.stop()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(run_headless())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.exception(f"Unhandled exception in headless runner: {e}")
        sys.exit(1)