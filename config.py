"""
Configuration module for the Telegram Content Migration Bot.
Loads and validates environment variables from .env and configures application logging.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load environment variables from .env file explicitly (repo dir, cwd, and /kaggle/working)
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)
load_dotenv()
if Path("/kaggle/working/.env").exists():
    load_dotenv(dotenv_path="/kaggle/working/.env")
if (Path(__file__).resolve().parent.parent / ".env").exists():
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


class Config:
    """Application configuration container."""

    # Telegram API credentials (from https://my.telegram.org)
    API_ID_RAW = (os.getenv("API_ID") or "").strip()
    API_HASH = (os.getenv("API_HASH") or "").strip()

    # Telegram Bot Token (from @BotFather)
    BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

    # Authorized Owner Telegram User ID (from @userinfobot)
    OWNER_ID_RAW = (os.getenv("OWNER_ID") or "").strip()

    # Optional Phone Number (e.g. +1234567890)
    PHONE_NUMBER = os.getenv("PHONE_NUMBER", "").strip() or None

    # Work Directories
    BASE_DIR = Path(__file__).resolve().parent
    DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")).resolve()
    SESSION_DIR = BASE_DIR

    # Logging settings
    LOG_FILE = os.getenv("LOG_FILE", "migration.log")
    LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()

    # Migration throttle & limits
    PROGRESS_INTERVAL = int(os.getenv("PROGRESS_INTERVAL", "10"))
    MIN_DELAY_SECONDS = float(os.getenv("MIN_DELAY_SECONDS", "1.0"))
    MAX_DELAY_SECONDS = float(os.getenv("MAX_DELAY_SECONDS", "3.0"))
    FLOOD_WAIT_MAX_SLEEP = int(os.getenv("FLOOD_WAIT_MAX_SLEEP", "300"))

    # Parallel MTProto Uploading & Downloading (Optimized for Telegram Premium & High-Throughput: 6-8 workers)
    _default_workers = max(6, min((os.cpu_count() or 2) * 2, 8))
    MAX_UPLOAD_WORKERS = int(os.getenv("MAX_UPLOAD_WORKERS", str(_default_workers)))

    # Video Anti-Theft Watermark Settings
    ENABLE_WATERMARK = os.getenv("ENABLE_WATERMARK", "false").lower() in ("true", "1", "yes")
    WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "@CourseVerseHere")
    WATERMARK_MODE = os.getenv("WATERMARK_MODE", "moving")

    # Custom Brand Thumbnail
    CUSTOM_THUMBNAIL_PATH = os.getenv("CUSTOM_THUMBNAIL_PATH", "")

    # Community & Support URLs
    SUPPORT_GROUP_URL = os.getenv("SUPPORT_GROUP_URL", "https://t.me/CourseVerseHere")
    UPDATES_CHANNEL_URL = os.getenv("UPDATES_CHANNEL_URL", "https://t.me/CourseVerseHere")

    # Session file names
    USERBOT_SESSION_NAME = "userbot"
    BOT_SESSION_NAME = "bot"
    LOCK_FILE_NAME = ".bot.lock"

    # Parsed numerical fields
    try:
        API_ID: int = int(API_ID_RAW) if API_ID_RAW else 0
    except ValueError:
        API_ID: int = 0

    try:
        OWNER_ID: int = int(OWNER_ID_RAW) if OWNER_ID_RAW else 0
    except ValueError:
        OWNER_ID: int = 0

    @classmethod
    def reload(cls) -> None:
        """Explicitly reload environment variables from all possible .env paths with override=True."""
        paths_to_check = [
            Path("/kaggle/working/tg-course-bot/.env"),
            Path("/kaggle/working/.env"),
            Path(__file__).resolve().parent / ".env",
            Path.cwd() / ".env",
            Path.home() / "tg-course-bot/.env",
            Path(__file__).resolve().parent.parent / ".env"
        ]
        for p in paths_to_check:
            if p.exists():
                load_dotenv(dotenv_path=p, override=True)

        load_dotenv(override=True)

        cls.API_ID_RAW = (os.getenv("API_ID") or "").strip()
        cls.API_HASH = (os.getenv("API_HASH") or "").strip()
        cls.BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
        cls.OWNER_ID_RAW = (os.getenv("OWNER_ID") or "").strip()
        cls.PHONE_NUMBER = os.getenv("PHONE_NUMBER", "").strip() or None

        try:
            cls.API_ID = int(cls.API_ID_RAW) if cls.API_ID_RAW else 0
        except ValueError:
            cls.API_ID = 0

        try:
            cls.OWNER_ID = int(cls.OWNER_ID_RAW) if cls.OWNER_ID_RAW else 0
        except ValueError:
            cls.OWNER_ID = 0

    @classmethod
    def validate(cls) -> None:
        """Validate required configuration values and parse types."""
        cls.reload()
        errors = []

        if not cls.API_ID_RAW or cls.API_ID == 0:
            errors.append(f"API_ID is missing or 0. Value: '{cls.API_ID_RAW}'. Obtain it from https://my.telegram.org")

        if not cls.API_HASH:
            errors.append("API_HASH is missing. Obtain it from https://my.telegram.org")

        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is missing. Obtain it from @BotFather on Telegram")

        if not cls.OWNER_ID_RAW or cls.OWNER_ID == 0:
            errors.append(f"OWNER_ID is missing or 0. Value: '{cls.OWNER_ID_RAW}'. Find your numerical ID via @userinfobot")

        if errors:
            print("\n" + "=" * 60, file=sys.stderr)
            print("CONFIGURATION ERROR(S) DETECTED:", file=sys.stderr)
            for err in errors:
                print(f"  ❌ {err}", file=sys.stderr)
            print("\nPlease check your .env file in /kaggle/working/tg-course-bot/.env", file=sys.stderr)
            print("=" * 60 + "\n", file=sys.stderr)
            sys.exit(1)

        # Ensure download directory exists
        cls.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class FlushStreamHandler(logging.StreamHandler):
    """Guarantees instant unbuffered log flushing to terminal/console."""
    def emit(self, record):
        super().emit(record)
        self.flush()


class SuppressPyrogramSocketFilter(logging.Filter):
    """Filters out internal raw socket disconnect/reconnect noise, closed db traces, and handle_updates background noise."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "socket.send() raised exception" in msg:
            return False
        if "Cannot operate on a closed database" in msg:
            return False
        if "Broken pipe" in msg and ("Retrying" in msg or "OSError" in msg):
            return False
        if "handle_updates" in msg or "Peer id invalid" in msg:
            return False
        if "Task exception was never retrieved" in msg:
            return False
        return True


def setup_logging() -> logging.Logger:
    """
    Set up dual logging to both stdout (console) and rotating log file.
    Returns the root application logger.
    """
    log_level = getattr(logging, Config.LOG_LEVEL_STR, logging.INFO)

    # Root logger
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if logger.handlers:
        return logging.getLogger("migration_bot")

    # Log formats
    file_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S"
    )

    socket_filter = SuppressPyrogramSocketFilter()

    # Console Handler (Instant Unbuffered Flush)
    console_handler = FlushStreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_format)
    console_handler.addFilter(socket_filter)
    logger.addHandler(console_handler)

    # Rotating File Handler (5 MB max, keeps 3 backups)
    log_path = Config.BASE_DIR / Config.LOG_FILE
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_format)
    file_handler.addFilter(socket_filter)
    logger.addHandler(file_handler)

    # Suppress excessive logging from external libraries like Pyrogram / PyCryptodome
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("pyrogram.connection").setLevel(logging.ERROR)
    logging.getLogger("pyrogram.session").setLevel(logging.ERROR)
    logging.getLogger("pyrogram.crypto").setLevel(logging.ERROR)

    app_logger = logging.getLogger("migration_bot")
    app_logger.info(f"Logging initialized. Level: {Config.LOG_LEVEL_STR}. File: {log_path}")
    return app_logger
