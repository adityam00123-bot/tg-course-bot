"""
Telegram Cloud Fast Uploader for Web & Course Downloader Bot.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional, Callable
from pyrogram import Client, enums
from pyrogram.types import Message

logger = logging.getLogger("course_uploader")


class TelegramCourseUploader:
    """Uploads downloaded lectures and notes directly to Telegram channels/users."""

    @staticmethod
    async def upload_video(
        client: Client,
        chat_id: int | str,
        video_path: Path,
        caption: str,
        thumb_path: Optional[Path] = None,
        duration: int = 0,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> Optional[Message]:
        """Upload streamable video lecture with custom thumbnail and progress tracking."""
        if not video_path.exists():
            return None

        safe_thumb = str(thumb_path) if (thumb_path and thumb_path.exists()) else None

        try:
            return await client.send_video(
                chat_id=chat_id,
                video=str(video_path),
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                thumb=safe_thumb,
                duration=duration,
                supports_streaming=True,
                progress=progress_cb
            )
        except Exception as e:
            logger.error(f"Failed to upload video {video_path}: {e}")
            return None

    @staticmethod
    async def upload_document(
        client: Client,
        chat_id: int | str,
        doc_path: Path,
        caption: str,
        file_name: Optional[str] = None,
        thumb_path: Optional[Path] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> Optional[Message]:
        """Upload PDF study material or document with progress tracking."""
        if not doc_path.exists():
            return None

        safe_thumb = str(thumb_path) if (thumb_path and thumb_path.exists()) else None
        target_file_name = file_name or doc_path.name

        try:
            return await client.send_document(
                chat_id=chat_id,
                document=str(doc_path),
                file_name=target_file_name,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                thumb=safe_thumb,
                force_document=True,
                progress=progress_cb
            )
        except Exception as e:
            logger.error(f"Failed to upload document {doc_path}: {e}")
            return None
