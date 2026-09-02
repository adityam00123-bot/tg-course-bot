"""
Main entry point for the Telegram Content Migration Bot.
Initializes configuration, acquires instance lock, handles interactive terminal login
for the Userbot on first run, performs peer synchronization, registers bot handlers,
and runs the service until interrupted.
"""

import os
import sys
import signal
import asyncio
import logging

# Force unbuffered real-time stdout streaming
os.environ["PYTHONUNBUFFERED"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Enable high-performance C-based uvloop on Linux (Kaggle) if installed
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except (ImportError, AttributeError):
    pass

# Ensure an active event loop exists for Python 3.12+ compatibility with Pyrogram
try:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
except Exception:
    pass

from pyrogram import idle
from pyrogram.types import BotCommand
from pyrogram.errors import (
    AccessTokenInvalid,
    AuthKeyDuplicated,
    SessionPasswordNeeded,
    FloodWait
)

from config import Config, setup_logging
from client import (
    LockManager,
    create_bot_client,
    get_or_create_user_client,
    sync_user_dialogs
)
from migration import MigrationEngine, get_user_engine
from handlers import register_handlers, build_welcome_keyboard


logger = logging.getLogger("migration_bot.main")


def print_startup_banner() -> None:
    """Print clean startup information banner in console."""
    Config.reload()
    api_id_str = str(Config.API_ID)
    masked_api = f"{api_id_str[:3]}****{api_id_str[-2:]}" if len(api_id_str) > 4 else api_id_str
    print("\n" + "=" * 65)
    print("      >> TELEGRAM CONTENT MIGRATION BOT (Pyrogram) <<      ")
    print("=" * 65)
    print(f" * Log File:        {Config.LOG_FILE}")
    print(f" * Download Temp:   {Config.DOWNLOAD_DIR}")
    print(f" * Telegram API ID: {masked_api}")
    print(f" * Owner User ID:   {Config.OWNER_ID}")
    print(f" * Progress Batch:  Every {Config.PROGRESS_INTERVAL} messages")
    print("=" * 65 + "\n")


async def main() -> None:
    """Main asynchronous orchestrator."""
    # 1. Validate environment configuration
    Config.validate()

    # 2. Configure dual console & file logging
    setup_logging()
    print_startup_banner()

    # 3. Acquire process instance lock to prevent duplicate polling conflicts
    lock_mgr = LockManager()
    if not lock_mgr.acquire():
        logger.error("Exiting due to duplicate instance lock conflict.")
        sys.exit(1)

    # 4. Instantiate Pyrogram Bot client
    bot = create_bot_client()
    owner_userbot = None

    try:
        # 5. Connect Default Owner Userbot Client (if session exists)
        logger.info("Initializing Userbot session manager...")
        owner_userbot = await get_or_create_user_client(Config.OWNER_ID)
        if owner_userbot:
            me_user = await owner_userbot.get_me()
            user_name = f"{me_user.first_name or ''} {me_user.last_name or ''}".strip()
            logger.info(f"✅ Owner Userbot logged in: {user_name} (@{me_user.username or 'N/A'}, ID: {me_user.id})")
            await sync_user_dialogs(Config.OWNER_ID, max_channels=20)
        else:
            logger.info("ℹ️ No default owner userbot session found. Users can login via /settings.")

        # 6. Register Bot UI Handlers
        register_handlers(bot)

        # 7. Start Bot Client
        logger.info("Connecting Bot client...")
        try:
            await bot.start()
        except AccessTokenInvalid:
            logger.error("❌ BOT_TOKEN is invalid. Please check your token from @BotFather in .env.")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Failed to start Bot client: {e}", exc_info=True)
            raise

        me_bot = await bot.get_me()
        logger.info(f"✅ Bot client online as: @{me_bot.username} (ID: {me_bot.id})")

        # 8. Register bot menu commands for Telegram '/' suggestion list
        try:
            await bot.set_bot_commands([
                BotCommand("start", "Open Migration Control Panel & Dashboard"),
                BotCommand("status", "Check Live Migration Progress & Statistics"),
                BotCommand("scan", "Scan Channel & Calculate Total Size (GB)"),
                BotCommand("run", "Start/Resume Migration Job"),
                BotCommand("stop", "Stop Active Migration Job"),
                BotCommand("help", "View Step-by-Step Guide & Link Formats"),
                BotCommand("cancel", "Cancel Current Input Step")
            ])
            logger.debug("Registered Telegram bot commands.")
        except Exception as e:
            logger.warning(f"Could not set bot commands: {e}")

        # 9. Send startup notification to all registered users on Telegram
        import database
        all_registered = database.get_all_users()
        user_ids_to_notify = set([Config.OWNER_ID])
        for uid_str in all_registered.keys():
            try:
                user_ids_to_notify.add(int(uid_str))
            except ValueError:
                pass

        broadcast_msg = (
            "🤖 <b>Telegram Content Migration Bot is now ONLINE!</b>\n\n"
            "⚡ The bot has restarted and is active for 24/7 high-speed channel forwarding.\n"
            "🚀 <i>Tap the button below to start:</i>"
        )

        for uid in user_ids_to_notify:
            try:
                user_kb = build_welcome_keyboard(uid)
                await bot.send_message(
                    chat_id=uid,
                    text=broadcast_msg,
                    reply_markup=user_kb
                )
                logger.info(f"Sent online notification to user ID: {uid}")
            except Exception as e:
                logger.debug(f"Could not send startup message to user {uid}: {e}")

        # 10. Start lightweight HTTP health server for 24/7 Cloud Platforms (HuggingFace / Render / Koyeb)
        port = int(os.environ.get("PORT", "7860"))
        try:
            async def _health_handler(reader, writer):
                await reader.read(1024)
                body = b'{"status":"ok","bot":"courseverse","version":"5.0"}'
                resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                writer.write(resp)
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            await asyncio.start_server(_health_handler, "0.0.0.0", port)
            logger.info(f"🌐 Cloud Health Server active on port {port} (Hugging Face / Cloud ready).")
        except Exception as h_err:
            logger.debug(f"Cloud health server not bound: {h_err}")

        # 11. Run until interrupted (SIGINT / SIGTERM)
        await idle()

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.error(f"Unexpected error in main event loop: {e}", exc_info=True)
    finally:
        logger.info("Initiating graceful shutdown...")

        from migration import USER_ENGINES
        for eng in USER_ENGINES.values():
            if eng.is_busy():
                eng.cancel_job()

        # Stop clients
        if bot and getattr(bot, "is_connected", False):
            try:
                await bot.stop()
                logger.info("Bot client stopped.")
            except Exception as e:
                logger.warning(f"Error stopping bot client: {e}")

        if owner_userbot and getattr(owner_userbot, "is_connected", False):
            try:
                await owner_userbot.stop()
                logger.info("Owner Userbot client stopped.")
            except Exception as e:
                logger.warning(f"Error stopping userbot client: {e}")

        # Release instance lock
        lock_mgr.release()
        logger.info("Cleanup complete. Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
