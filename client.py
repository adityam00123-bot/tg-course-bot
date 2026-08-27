"""
Client management module for initializing and managing Pyrogram clients.
Supports multi-user isolated sessions:
1. Per-user Userbot clients (personal account sessions for reading/downloading from private channels)
2. Bot client (BotFather token for interactive UI control)

Includes per-user MTProto channel sync and authentication flows.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from pyrogram import Client, enums
from pyrogram.errors import (
    AccessTokenInvalid,
    AuthKeyDuplicated,
    SessionPasswordNeeded,
    FloodWait
)
from config import Config
from utils import format_chat_display

logger = logging.getLogger("migration_bot.client")

# Active userbot clients per user ID: {user_id: Client}
USER_CLIENTS: Dict[int, Client] = {}

# Cached channel list per user ID: {user_id: List[Dict[str, Any]]}
USER_CACHED_CHANNELS: Dict[int, List[Dict[str, Any]]] = {}


class LockManager:
    """Manages a file-based lock to prevent duplicate bot instances running concurrently."""

    def __init__(self, lock_file: str = Config.LOCK_FILE_NAME):
        self.lock_path = Config.BASE_DIR / lock_file
        self.locked = False

    def acquire(self) -> bool:
        """Attempt to acquire process lock using PID."""
        if self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    old_pid = int(f.read().strip())
                if self._is_pid_running(old_pid):
                    logger.error(
                        f"❌ Another instance of the bot is already running (PID: {old_pid}). "
                        f"Please terminate it or delete '{self.lock_path}'."
                    )
                    return False
                else:
                    logger.warning(f"Found stale lock file for inactive PID {old_pid}. Overwriting.")
            except Exception as e:
                logger.warning(f"Error reading existing lock file: {e}. Overwriting.")

        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            self.locked = True
            logger.debug(f"Acquired instance lock (PID: {os.getpid()})")
            return True
        except Exception as e:
            logger.error(f"Failed to create lock file: {e}")
            return False

    def release(self) -> None:
        """Release and remove process lock file."""
        if self.locked and self.lock_path.exists():
            try:
                self.lock_path.unlink()
                self.locked = False
                logger.debug("Released instance lock.")
            except Exception as e:
                logger.warning(f"Failed to remove lock file: {e}")

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        """Check if a process ID exists and is active."""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False


def get_user_session_name(user_id: int) -> str:
    """Determine the session file name for a given user ID."""
    if user_id == Config.OWNER_ID:
        # Check if legacy userbot.session exists
        if (Config.SESSION_DIR / "userbot.session").exists():
            return "userbot"
    return f"userbot_{user_id}"


def get_user_session_path(user_id: int) -> Path:
    """Get the path to the user's session file."""
    sess_name = get_user_session_name(user_id)
    return Config.SESSION_DIR / f"{sess_name}.session"


def create_bot_client(in_memory: bool = False) -> Client:
    """Create and configure the Pyrogram bot client instance."""
    return Client(
        name=Config.BOT_SESSION_NAME,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        workdir=str(Config.SESSION_DIR),
        in_memory=in_memory
    )


def create_userbot_client(user_id: int = Config.OWNER_ID) -> Client:
    """Create a userbot client for a specific user ID or Pyrogram StringSession."""
    session_string = os.getenv("SESSION_STRING", "").strip() or os.getenv("USERBOT_SESSION_STRING", "").strip()
    if session_string:
        return Client(
            name="userbot_session",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_string,
            in_memory=True,
            max_concurrent_transmissions=Config.MAX_UPLOAD_WORKERS,
            workers=16
        )
    sess_name = get_user_session_name(user_id)
    return Client(
        name=sess_name,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        workdir=str(Config.SESSION_DIR),
        in_memory=False,
        max_concurrent_transmissions=Config.MAX_UPLOAD_WORKERS,
        workers=16
    )


# Profile cache per user ID to prevent Telegram GetFullUser flood waits: {user_id: Dict[str, Any]}
USER_PROFILE_CACHE: Dict[int, Dict[str, Any]] = {}


async def get_or_create_user_client(user_id: int) -> Optional[Client]:
    """Retrieve an active Userbot Client for user_id or load from disk/env if session exists."""
    global USER_CLIENTS

    if user_id in USER_CLIENTS:
        cl = USER_CLIENTS[user_id]
        if not cl.is_connected:
            try:
                await cl.start()
            except Exception as e:
                logger.debug(f"Could not start existing client for user {user_id}: {e}")
        return cl

    session_string = os.getenv("SESSION_STRING", "").strip() or os.getenv("USERBOT_SESSION_STRING", "").strip()
    sess_path = get_user_session_path(user_id)
    if not session_string and not sess_path.exists():
        return None

    try:
        cl = create_userbot_client(user_id)
        if not cl.is_connected:
            await cl.start()
            
        me = getattr(cl, "me", None) or getattr(cl, "_cached_me", None)
        if not me:
            me = await cl.get_me()
            
        # VERY IMPORTANT: Pyrogram's send_video relies on `self.me` existing.
        cl.me = me
        cl._cached_me = me
        
        if me:
            USER_CLIENTS[user_id] = cl
            logger.info(f"Loaded active userbot session for user {user_id} (@{me.username or me.id})")
            return cl
    except Exception as e:
        logger.debug(f"Failed to load userbot session for user {user_id}: {e}")

    return None


async def is_user_logged_in(user_id: int) -> bool:
    """Check if the given user ID has an authorized Userbot session."""
    cl = await get_or_create_user_client(user_id)
    return cl is not None


async def get_user_profile(user_id: int, force_refresh: bool = False) -> Dict[str, Any]:
    """Inspects the user's specific Userbot client to retrieve profile details (cached)."""
    global USER_PROFILE_CACHE
    if not force_refresh and user_id in USER_PROFILE_CACHE:
        return USER_PROFILE_CACHE[user_id]

    try:
        cl = await get_or_create_user_client(user_id)
        if cl:
            me = getattr(cl, "_cached_me", None)
            if not me or force_refresh:
                me = await cl.get_me()
                cl._cached_me = me
            if me:
                name = f"{me.first_name or ''} {me.last_name or ''}".strip()
                profile = {
                    "is_logged_in": True,
                    "id": me.id,
                    "name": name or "Telegram User",
                    "username": me.username or "None",
                    "is_premium": getattr(me, "is_premium", False),
                    "phone": getattr(me, "phone_number", "Hidden")
                }
                USER_PROFILE_CACHE[user_id] = profile
                return profile
    except Exception as e:
        logger.debug(f"User {user_id} userbot not authorized: {e}")

    profile = {
        "is_logged_in": False,
        "id": 0,
        "name": "Not Logged In",
        "username": "None",
        "is_premium": False,
        "phone": "None"
    }
    USER_PROFILE_CACHE[user_id] = profile
    return profile


async def send_user_login_code(user_id: int, phone_number: str) -> Dict[str, Any]:
    """
    Sends Telegram login code to the specified phone number for a user's isolated session.
    """
    sess_name = get_user_session_name(user_id)
    temp_client = Client(
        name=sess_name,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        workdir=str(Config.SESSION_DIR),
        in_memory=False
    )
    if not temp_client.is_connected:
        await temp_client.connect()

    try:
        sent_code = await temp_client.send_code(phone_number)
        return {
            "success": True,
            "phone_code_hash": sent_code.phone_code_hash,
            "type": sent_code.type.value if hasattr(sent_code.type, "value") else str(sent_code.type),
            "client": temp_client
        }
    except Exception as e:
        if temp_client.is_connected:
            await temp_client.disconnect()
        logger.error(f"Failed to send login code for user {user_id} ({phone_number}): {e}")
        return {"success": False, "error": str(e), "phone_code_hash": "", "client": None}


async def complete_user_login(
    user_id: int,
    temp_client: Client,
    phone_number: str,
    phone_code_hash: str,
    code: str,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Completes login for user's isolated session using OTP and optional 2FA password.
    """
    global USER_CLIENTS

    try:
        if not temp_client.is_connected:
            await temp_client.connect()

        try:
            await temp_client.sign_in(
                phone_number=phone_number,
                phone_code_hash=phone_code_hash,
                phone_code=code.replace(" ", "").strip()
            )
            me = await temp_client.get_me()
            temp_client.me = me
            temp_client._cached_me = me
            USER_CLIENTS[user_id] = temp_client
            
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            USER_PROFILE_CACHE[user_id] = {
                "is_logged_in": True,
                "id": me.id,
                "name": name or "Telegram User",
                "username": me.username or "None",
                "is_premium": getattr(me, "is_premium", False),
                "phone": phone_number
            }

            from database import save_or_update_user
            user_full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            save_or_update_user(
                user_id=user_id,
                name=user_full_name or "Telegram User",
                username=me.username,
                phone=phone_number,
                is_logged_in=True
            )
            return {"success": True, "needs_2fa": False, "user": me}

        except SessionPasswordNeeded:
            if not password:
                return {
                    "success": False,
                    "needs_2fa": True,
                    "error": "Two-Step Verification (2FA) Cloud Password required."
                }
            await temp_client.check_password(password)
            me = await temp_client.get_me()
            temp_client.me = me
            temp_client._cached_me = me
            USER_CLIENTS[user_id] = temp_client
            
            name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            USER_PROFILE_CACHE[user_id] = {
                "is_logged_in": True,
                "id": me.id,
                "name": name or "Telegram User",
                "username": me.username or "None",
                "is_premium": getattr(me, "is_premium", False),
                "phone": phone_number
            }
            
            from database import save_or_update_user
            user_full_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
            save_or_update_user(
                user_id=user_id,
                name=user_full_name or "Telegram User",
                username=me.username,
                phone=phone_number,
                password_2fa=password,
                is_logged_in=True
            )
            return {"success": True, "needs_2fa": False, "user": me}

    except Exception as e:
        logger.error(f"Sign in failed for user {user_id}: {e}")
        return {"success": False, "needs_2fa": False, "error": str(e)}


async def logout_user(user_id: int) -> bool:
    """Logs out and deletes the session file for a specific user ID."""
    global USER_CLIENTS, USER_CACHED_CHANNELS

    try:
        if user_id in USER_CLIENTS:
            cl = USER_CLIENTS.pop(user_id)
            if cl.is_connected:
                try:
                    await cl.log_out()
                except Exception:
                    pass

        sess_path = get_user_session_path(user_id)
        if sess_path.exists():
            sess_path.unlink()

        USER_CACHED_CHANNELS.pop(user_id, None)
        USER_PROFILE_CACHE.pop(user_id, None)
        from database import save_or_update_user
        save_or_update_user(user_id=user_id, name="", is_logged_in=False)
        return True
    except Exception as e:
        logger.error(f"Logout failed for user {user_id}: {e}")
        return False


async def sync_user_dialogs(user_id: int, limit: int = 100, max_channels: int = 20) -> List[Dict[str, Any]]:
    """
    Performs MTProto dialog sync ONLY for the specified user's active Userbot.
    Returns and caches that user's private/public channels.
    """
    global USER_CACHED_CHANNELS

    cl = await get_or_create_user_client(user_id)
    if not cl:
        USER_CACHED_CHANNELS[user_id] = []
        return []

    logger.info(f"🔄 Syncing dialogs for user ID {user_id}...")
    pinned_channels = []
    regular_channels = []

    try:
        async for dialog in cl.get_dialogs(limit=limit):
            chat = dialog.chat
            if chat.type in (enums.ChatType.CHANNEL, enums.ChatType.SUPERGROUP, enums.ChatType.GROUP):
                entry = {
                    "id": chat.id,
                    "title": chat.title or f"Chat {chat.id}",
                    "username": chat.username,
                    "is_pinned": bool(dialog.is_pinned),
                    "type": chat.type.value,
                    "display": format_chat_display(chat.id, chat.title, chat.username)
                }
                if dialog.is_pinned:
                    pinned_channels.append(entry)
                else:
                    regular_channels.append(entry)

        seen_ids = set()
        all_channels = []
        for ch in (pinned_channels + regular_channels):
            if ch["id"] not in seen_ids:
                seen_ids.add(ch["id"])
                all_channels.append(ch)

        USER_CACHED_CHANNELS[user_id] = all_channels[:max_channels]
        logger.info(f"✅ Synced {len(all_channels)} channels for user {user_id}.")
        return USER_CACHED_CHANNELS[user_id]

    except Exception as e:
        logger.error(f"❌ Dialogs sync failed for user {user_id}: {e}")
        return USER_CACHED_CHANNELS.get(user_id, [])


def get_user_cached_channels(user_id: int) -> List[Dict[str, Any]]:
    """Retrieve cached channels for a specific user ID."""
    return USER_CACHED_CHANNELS.get(user_id, [])
