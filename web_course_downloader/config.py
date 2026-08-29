"""
Configuration module for Web & Classplus Course Downloader Telegram Bot.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
_BASE_DIR = Path(__file__).resolve().parent
_ENV_PATH = _BASE_DIR / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


class Config:
    """Bot and Engine configuration container."""
    
    BASE_DIR = _BASE_DIR
    DOWNLOAD_DIR = _BASE_DIR / "downloads"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    
    # Telegram API Credentials
    API_ID = int((os.getenv("API_ID") or "22152865").strip())
    API_HASH = (os.getenv("API_HASH") or "0628e97cf87f63fa942c75a4dc248db3").strip()
    BOT_TOKEN = (os.getenv("BOT_TOKEN") or "8845853554:AAH3k86jW0qN7c2Y-1s0a-Ksq_46UfW9n28").strip()
    OWNER_ID = int((os.getenv("OWNER_ID") or "8383627571").strip())
    SESSION_STRING = (os.getenv("SESSION_STRING") or "").strip()
    
    # Optional Custom Thumbnail Path
    DEFAULT_THUMB = _BASE_DIR / "thumb.jpg"
    
    # Downloader Settings
    MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
    MAX_CONCURRENT_UPLOADS = int(os.getenv("MAX_CONCURRENT_UPLOADS", "2"))
    CHUNK_SIZE = 1024 * 1024  # 1 MB
