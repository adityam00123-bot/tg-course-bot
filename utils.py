"""
Utility functions for Telegram link parsing, caption sanitization,
channel formatting, and local file cleanup.
"""

import os
import re
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, Union

logger = logging.getLogger("migration_bot.utils")


@dataclass
class ParsedLink:
    """Structure holding parsed Telegram message link data."""
    chat_id: Union[int, str]
    start_msg_id: int
    end_msg_id: Optional[int] = None
    is_private: bool = False
    raw_input: str = ""


# Regex patterns for Telegram message links
# 1. Private channels/supergroups: https://t.me/c/1234567890/42 or https://t.me/c/1234567890/42-50
PRIVATE_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/c/(\d+)/(\d+)(?:-(\d+))?",
    re.IGNORECASE
)

# 2. Public channels/supergroups: https://t.me/username/42 or https://t.me/username/42-50
PUBLIC_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/([a-zA-Z0-9_]{4,})/(\d+)(?:-(\d+))?",
    re.IGNORECASE
)


def parse_telegram_link(text: str) -> Optional[ParsedLink]:
    """
    Parse a Telegram message link (public or private) or direct message ID.

    Supports:
    - https://t.me/c/1234567890/42 -> chat_id: -1001234567890, msg_id: 42
    - https://t.me/c/1234567890/10-25 -> chat_id: -1001234567890, start_id: 10, end_id: 25
    - https://t.me/mychannel/42 -> chat_id: "mychannel", msg_id: 42
    - https://t.me/mychannel/10-25 -> chat_id: "mychannel", start_id: 10, end_id: 25
    - "42" (standalone integer message ID)

    Returns:
        ParsedLink dataclass or None if parsing fails.
    """
    if not text:
        return None

    cleaned = text.strip()

    # 1. Check for private channel link (/c/<id>/<msg_id>)
    priv_match = PRIVATE_LINK_PATTERN.search(cleaned)
    if priv_match:
        raw_chat_id = priv_match.group(1)
        start_id = int(priv_match.group(2))
        end_id = int(priv_match.group(3)) if priv_match.group(3) else None

        # Convert raw channel ID to Pyrogram -100... channel peer ID format
        chat_id = int(f"-100{raw_chat_id}")
        return ParsedLink(
            chat_id=chat_id,
            start_msg_id=start_id,
            end_msg_id=end_id,
            is_private=True,
            raw_input=cleaned
        )

    # 2. Check for public channel link (/<username>/<msg_id>)
    pub_match = PUBLIC_LINK_PATTERN.search(cleaned)
    if pub_match:
        username = pub_match.group(1)
        # Avoid matching system URLs like /c/, /joinchat/, /addstickers/
        if username.lower() not in ("c", "joinchat", "addstickers", "s", "share", "iv", "login"):
            start_id = int(pub_match.group(2))
            end_id = int(pub_match.group(3)) if pub_match.group(3) else None
            return ParsedLink(
                chat_id=username,
                start_msg_id=start_id,
                end_msg_id=end_id,
                is_private=False,
                raw_input=cleaned
            )

    # 3. Check for direct numerical message ID (e.g. "42" or "100")
    if cleaned.isdigit():
        msg_id = int(cleaned)
        if msg_id > 0:
            return ParsedLink(
                chat_id="",
                start_msg_id=msg_id,
                end_msg_id=None,
                is_private=False,
                raw_input=cleaned
            )

    return None


def parse_message_id_or_link(text: str) -> Optional[Tuple[Optional[Union[int, str]], int]]:
    """
    Convenience parser that extracts (chat_id, msg_id) from link or direct ID.
    Returns (chat_id, msg_id) or None.
    """
    parsed = parse_telegram_link(text)
    if parsed:
        chat = parsed.chat_id if parsed.chat_id != "" else None
        return chat, parsed.start_msg_id
    return None


# Patterns used for caption sanitization
FORWARD_PATTERNS = [
    re.compile(r"^\[?Forwarded from\s+[^\]\n]+\]?", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Forwarded Message:\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Fwd:\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^↪️\s*Forwarded from\s+[^\n]+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^From:\s+@[a-zA-Z0-9_]+", re.IGNORECASE | re.MULTILINE),
]


def sanitize_caption(caption: Optional[str]) -> Optional[str]:
    """
    Sanitize message captions by stripping forwarding headers while preserving
    original educational formatting, text, and markdown.

    Returns:
        Cleaned string or None if empty.
    """
    if not caption:
        return None

    cleaned = caption
    for pattern in FORWARD_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Clean leading/trailing whitespaces and redundant consecutive blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    return cleaned if cleaned else None


def format_chat_display(chat_id: Union[int, str], title: Optional[str] = None, username: Optional[str] = None) -> str:
    """
    Format a chat/channel for user-friendly UI display.
    """
    if title and username:
        return f"{title} (@{username})"
    if title:
        return title
    if username:
        return f"@{username}"
    return f"Chat [{chat_id}]"


def cleanup_temp_file(path: Union[str, Path]) -> None:
    """
    Safely delete a temporary downloaded file with Windows retry support.
    """
    if not path:
        return

    try:
        p = Path(path)
        if p.exists() and p.is_file():
            for _ in range(3):
                try:
                    p.unlink(missing_ok=True)
                    logger.debug(f"Removed temporary file: {p}")
                    return
                except (PermissionError, OSError):
                    time.sleep(0.1)
    except Exception as e:
        logger.debug(f"Temporary file cleanup deferred for '{path}': {e}")


def format_seconds(seconds: float) -> str:
    """Format duration in human-friendly string (e.g. 2m 15s)."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rem_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {rem_seconds}s"
    hours = minutes // 60
    rem_minutes = minutes % 60
    return f"{hours}h {rem_minutes}m {rem_seconds}s"


def format_progress_bar(current: int, total: int, length: int = 10) -> str:
    """Generate visual ASCII progress bar."""
    if total <= 0:
        return "[░░░░░░░░░░] 0%"
    percent = min(1.0, max(0.0, current / total))
    filled_len = int(length * percent)
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}] {int(percent * 100)}%"
