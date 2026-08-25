"""
Core migration engine for transferring Telegram content between channels.
Executes a download-then-upload pipeline to bypass channel forwarding/copy restrictions,
sanitizes captions, respects flood limits, and provides real-time progress updates.
Includes Auto-Resume checkpointing and Concurrent Prefetching Pipeline.
"""

import os
import re
import shutil
import zipfile
import json
import time
import random
import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union, Callable, Awaitable, List, Any, Dict, Tuple

from pyrogram import Client, enums, raw
from pyrogram.types import Message
from pyrogram.errors import (
    RPCError,
    FloodWait,
    ChatForwardsRestricted,
    MediaEmpty,
    MessageEmpty,
    ChannelInvalid,
    ChatAdminRequired,
    PeerIdInvalid
)

from config import Config
from utils import (
    sanitize_caption,
    cleanup_temp_file,
    format_seconds,
    format_progress_bar
)
from watermark import (
    remove_or_mask_watermark,
    apply_video_watermark,
    extract_video_thumbnail,
    remux_to_streamable_mp4,
    is_ffmpeg_available
)
from fast_uploader import install_fast_uploader

logger = logging.getLogger("migration_bot.migration")

CHECKPOINT_FILE = Config.BASE_DIR / "migration_progress.json"


def natural_sort_key(s: str) -> list:
    """Sort strings containing numbers naturally (e.g. 1, 2, 10 instead of 1, 10, 2)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def _get_checkpoint_key(source_id: Any, dest_id: Any) -> str:
    return f"{source_id}_{dest_id}"


def load_checkpoint(source_id: Any, dest_id: Any) -> int:
    """Load the last successfully processed message ID for this channel pair."""
    try:
        if CHECKPOINT_FILE.exists():
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get(_get_checkpoint_key(source_id, dest_id), 0))
    except Exception as e:
        logger.debug(f"Could not load checkpoint: {e}")
    return 0


def save_checkpoint(source_id: Any, dest_id: Any, msg_id: int) -> None:
    """Persist the last successfully processed message ID."""
    try:
        data = {}
        if CHECKPOINT_FILE.exists():
            try:
                with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[_get_checkpoint_key(source_id, dest_id)] = msg_id
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not save checkpoint: {e}")


def reset_checkpoint(source_id: Any, dest_id: Any) -> None:
    """Clear checkpoint for this channel pair."""
    try:
        if CHECKPOINT_FILE.exists():
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = _get_checkpoint_key(source_id, dest_id)
            if key in data:
                del data[key]
                with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not reset checkpoint: {e}")


class EngineType(str, Enum):
    USERBOT = "userbot"
    BOT_ADMIN = "bot_admin"


class MigrationMode(str, Enum):
    FULL = "full"
    RANGE = "range"


class OutputFormat(str, Enum):
    VIDEO = "video"
    FILE = "file"


class CaptionMode(str, Enum):
    OFF = "off"
    APPEND = "append"
    REPLACE = "replace"
    REMOVE = "remove"


class JobStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass
class MigrationConfig:
    """Configuration state for a migration job."""
    source_chat_id: Optional[Union[int, str]] = None
    source_chat_title: Optional[str] = None
    dest_chat_id: Optional[Union[int, str]] = None
    dest_chat_title: Optional[str] = None
    engine_type: EngineType = EngineType.USERBOT
    mode: MigrationMode = MigrationMode.FULL
    start_msg_id: Optional[int] = None
    end_msg_id: Optional[int] = None
    output_format: OutputFormat = OutputFormat.VIDEO
    auto_extract_zip: bool = False
    enable_custom_thumbnail: bool = False
    strip_existing_thumbnail: bool = False
    enable_watermark: bool = False
    clean_old_watermark: bool = False
    clean_wm_position: str = 'bottom_right'
    clean_wm_style: str = 'delogo'
    custom_thumbnail_path: Optional[str] = None
    watermark_text: str = "@CourseVerseHere"
    watermark_mode: str = "moving"
    caption_mode: CaptionMode = CaptionMode.OFF
    custom_caption_text: str = ""


@dataclass
class MigrationStats:
    """Real-time statistics for an active or completed migration job."""
    status: JobStatus = JobStatus.IDLE
    total_messages: int = 0
    processed_count: int = 0
    media_count: int = 0
    text_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    total_bytes_migrated: int = 0
    current_msg_id: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error_message: Optional[str] = None

    @property
    def elapsed_seconds(self) -> float:
        if not self.start_time:
            return 0.0
        end = self.end_time if self.end_time else time.time()
        return max(0.0, end - self.start_time)

    @property
    def speed_mbps(self) -> float:
        if self.elapsed_seconds < 1 or self.total_bytes_migrated == 0:
            return 0.0
        return (self.total_bytes_migrated / (1024 * 1024)) / self.elapsed_seconds


@dataclass
class DeletionStats:
    """Real-time statistics for an active or completed deletion job."""
    status: JobStatus = JobStatus.IDLE
    total_messages: int = 0
    deleted_count: int = 0
    failed_count: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error_message: Optional[str] = None

    @property
    def elapsed_seconds(self) -> float:
        if not self.start_time:
            return 0.0
        end = self.end_time if self.end_time else time.time()
        return max(0.0, end - self.start_time)


class MigrationEngine:
    """
    Manages and executes content migration and channel deletion using Userbot & Bot clients.
    """

    def __init__(self, userbot: Optional[Client], bot: Client, owner_id: int):
        self.userbot = userbot
        self.bot = bot
        self.owner_id = owner_id
        self.config = MigrationConfig()
        self.stats = MigrationStats()
        self.cancel_event = asyncio.Event()
        self.progress_msg_id: Optional[int] = None
        self._running_task: Optional[asyncio.Task] = None

        # Deletion state
        self.deletion_stats = DeletionStats()
        self.deletion_target_chat: Optional[Union[int, str]] = None
        self.deletion_target_title: Optional[str] = None
        self.deletion_mode: MigrationMode = MigrationMode.FULL
        self.deletion_start_msg_id: Optional[int] = None
        self.deletion_end_msg_id: Optional[int] = None
        self.deletion_progress_msg_id: Optional[int] = None
        self.deletion_cancel_event = asyncio.Event()
        self._deletion_task: Optional[asyncio.Task] = None

        # Cache resolved peers across MTProto operations
        self._resolved_peers: Dict[Union[int, str], Any] = {}

        # Initialize default watermark & branding strictly from Config / filesystem (disabled by default for max throughput)
        self.config.enable_watermark = Config.ENABLE_WATERMARK
        self.config.watermark_text = Config.WATERMARK_TEXT or "@CourseVerseHere"
        self.config.watermark_mode = Config.WATERMARK_MODE or "moving"
        if Config.CUSTOM_THUMBNAIL_PATH and os.path.exists(Config.CUSTOM_THUMBNAIL_PATH):
            self.config.custom_thumbnail_path = Config.CUSTOM_THUMBNAIL_PATH
            self.config.enable_custom_thumbnail = True
        else:
            thumb_path = Config.BASE_DIR / "thumb.jpg"
            if thumb_path.exists() and self.owner_id == Config.OWNER_ID:
                self.config.custom_thumbnail_path = str(thumb_path)
                # Keep custom thumbnail False by default unless toggled in UI
                self.config.enable_custom_thumbnail = False

        if self.userbot:
            install_fast_uploader(self.userbot, max_workers=Config.MAX_UPLOAD_WORKERS)

    @property
    def client(self) -> Client:
        """Returns the appropriate Pyrogram Client for the active engine mode."""
        if self.config.engine_type == EngineType.USERBOT and self.userbot:
            return self.userbot
        return self.bot

    def is_busy(self) -> bool:
        """Check if a migration or deletion task is actively running."""
        return self.stats.status == JobStatus.RUNNING or self.deletion_stats.status == JobStatus.RUNNING

    def is_deleting(self) -> bool:
        """Check if a deletion task is actively running."""
        return self.deletion_stats.status == JobStatus.RUNNING

    def _apply_caption(self, original_caption: Optional[str], original_entities: Optional[list]) -> Tuple[Optional[str], Optional[list]]:
        """Applies caption mode transformations (append, replace, remove, off)."""
        mode = self.config.caption_mode
        text = self.config.custom_caption_text or ""
        if mode == CaptionMode.REMOVE:
            return None, None
        elif mode == CaptionMode.REPLACE:
            return (text if text else None), None
        elif mode == CaptionMode.APPEND:
            if not text:
                return original_caption, original_entities
            if original_caption:
                return f"{original_caption}\n\n{text}", None
            return text, None
        return original_caption, original_entities

    def set_caption(self, mode: CaptionMode, text: Optional[str] = None) -> None:
        self.config.caption_mode = mode
        if text is not None:
            self.config.custom_caption_text = text

    def set_deletion_target(self, chat_id: Union[int, str], title: Optional[str] = None) -> None:
        self.deletion_target_chat = chat_id
        self.deletion_target_title = title or str(chat_id)

    def set_deletion_range(self, start_id: int, end_id: int) -> None:
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        self.deletion_start_msg_id = start_id
        self.deletion_end_msg_id = end_id
        self.deletion_mode = MigrationMode.RANGE

    def set_deletion_full_mode(self) -> None:
        self.deletion_mode = MigrationMode.FULL
        self.deletion_start_msg_id = None
        self.deletion_end_msg_id = None

    def set_source(self, chat_id: Union[int, str], title: Optional[str] = None) -> None:
        self.config.source_chat_id = chat_id
        self.config.source_chat_title = title or str(chat_id)

    def set_destination(self, chat_id: Union[int, str], title: Optional[str] = None) -> None:
        self.config.dest_chat_id = chat_id
        self.config.dest_chat_title = title or str(chat_id)

    def set_range(self, start_id: int, end_id: int) -> None:
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        self.config.start_msg_id = start_id
        self.config.end_msg_id = end_id
        self.config.mode = MigrationMode.RANGE

    def set_full_channel_mode(self) -> None:
        self.config.mode = MigrationMode.FULL
        self.config.start_msg_id = None
        self.config.end_msg_id = None

    def set_output_format(self, output_format: OutputFormat) -> None:
        self.config.output_format = output_format

    def toggle_output_format(self) -> OutputFormat:
        if self.config.output_format == OutputFormat.VIDEO:
            self.config.output_format = OutputFormat.FILE
        else:
            self.config.output_format = OutputFormat.VIDEO
        return self.config.output_format

    def set_engine_type(self, engine_type: EngineType) -> None:
        self.config.engine_type = engine_type

    def toggle_engine_type(self) -> EngineType:
        if self.config.engine_type == EngineType.USERBOT:
            self.config.engine_type = EngineType.BOT_ADMIN
        else:
            self.config.engine_type = EngineType.USERBOT
        return self.config.engine_type

    def set_thumbnail(self, path: Optional[str] = None, enable: Optional[bool] = None, strip_existing: Optional[bool] = None) -> None:
        if path is not None:
            self.config.custom_thumbnail_path = path
        if enable is not None:
            self.config.enable_custom_thumbnail = enable
        if strip_existing is not None:
            self.config.strip_existing_thumbnail = strip_existing

    def set_watermark(self, text: Optional[str] = None, mode: Optional[str] = None, enable: Optional[bool] = None) -> None:
        if text is not None:
            self.config.watermark_text = text
        if mode is not None:
            self.config.watermark_mode = mode
        if enable is not None:
            self.config.enable_watermark = enable

    def clear_watermark(self) -> None:
        self.config.enable_watermark = False
        self.config.watermark_text = ""

    def cancel_job(self) -> bool:
        """Signals the running job to cancel."""
        if not self.is_busy():
            return False
        logger.info("Cancellation requested by user.")
        self.cancel_event.set()
        return True

    async def start_migration(self, progress_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
        """Initiates the migration job asynchronously."""
        if self.is_busy():
            raise RuntimeError("A migration job is already running.")

        if not self.config.source_chat_id or not self.config.dest_chat_id:
            raise ValueError("Both source and destination channels must be configured before starting.")

        if self.config.engine_type == EngineType.USERBOT:
            from client import get_or_create_user_client
            self.userbot = await get_or_create_user_client(self.owner_id)
            if not self.userbot:
                raise ValueError("⚠️ Userbot session is not active! Please log in using /settings or tap 'Login with Phone Number'.")
            install_fast_uploader(self.userbot, max_workers=Config.MAX_UPLOAD_WORKERS)

        self.cancel_event.clear()
        self.progress_msg_id = None
        self.stats = MigrationStats(status=JobStatus.RUNNING, start_time=time.time())

        # Launch pipeline as background task
        self._running_task = asyncio.create_task(
            self._run_migration_pipeline(progress_callback=progress_callback)
        )

    # Alias for start_migration
    start_job = start_migration

    async def _resolve_peer_cached(self, chat_id: Union[int, str]) -> Any:
        """Cache MTProto peer resolution in memory to prevent flood-waits."""
        if chat_id not in self._resolved_peers:
            self._resolved_peers[chat_id] = await self.client.resolve_peer(chat_id)
        return self._resolved_peers[chat_id]

    async def _execute_with_flood_retry(self, coro_fn: Callable, *args, **kwargs) -> Any:
        """
        Executes an asynchronous Pyrogram MTProto API call with automatic
        FloodWait backoff and transient network retry handling.
        """
        attempt = 0
        max_attempts = 10

        while attempt < max_attempts:
            if self.cancel_event.is_set():
                raise asyncio.CancelledError("Migration cancelled by user.")

            try:
                return await coro_fn(*args, **kwargs)

            except FloodWait as e:
                attempt += 1
                sleep_duration = min(e.value + 1, Config.FLOOD_WAIT_MAX_SLEEP)
                logger.warning(
                    f"⚠️ Telegram FloodWait: Sleeping for {sleep_duration}s "
                    f"(Attempt {attempt}/{max_attempts}). Action: {coro_fn.__name__}"
                )

                try:
                    await self.bot.send_message(
                        chat_id=self.owner_id,
                        text=(
                            f"⌛ <b>Telegram Rate-Limit Notice</b>\n\n"
                            f"Telegram requested a temporary pause. Sleeping for <b>{sleep_duration}s</b>.\n"
                            f"<i>Migration will automatically resume.</i>"
                        ),
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass

                await asyncio.sleep(sleep_duration)

            except (RPCError, TimeoutError, ConnectionError) as e:
                err_str = str(e).upper()
                if "MESSAGE_NOT_MODIFIED" in err_str:
                    return None

                non_retryable = [
                    "MESSAGE_ID_INVALID",
                    "MESSAGE_EMPTY",
                    "MEDIA_EMPTY",
                    "CHAT_FORWARDS_RESTRICTED",
                    "CHAT_ADMIN_REQUIRED",
                    "USER_BANNED_IN_CHANNEL",
                    "CHANNEL_PRIVATE",
                    "PEER_ID_INVALID",
                    "FILE_REFERENCE_EXPIRED",
                    "MESSAGE_NOT_MODIFIED"
                ]
                if any(nr in err_str for nr in non_retryable):
                    raise

                attempt += 1
                if attempt >= max_attempts:
                    logger.error(f"❌ Permanent RPC failure on {coro_fn.__name__}: {e}")
                    raise
                wait_sec = min(2 ** attempt, 30)
                logger.warning(f"Transient Telegram error on {coro_fn.__name__}: {e}. Retrying in {wait_sec}s...")
                await asyncio.sleep(wait_sec)

            except Exception as e:
                raise

        raise RuntimeError(f"Operation {coro_fn.__name__} failed after {max_attempts} flood wait retries.")

    async def _send_progress_update(self, is_final: bool = False) -> None:
        """Publishes a rich status report by editing a single live message in real-time."""
        try:
            total = self.stats.total_messages
            processed = self.stats.processed_count
            progress_bar = format_progress_bar(processed, total, length=12)

            speed_val = self.stats.speed_mbps
            speed_str = f"{speed_val:.1f} MB/s" if speed_val > 0 else "Instant / Active"

            # Compute estimated time left (ETA)
            if total > 0 and processed > 0 and speed_val > 0:
                remaining_msgs = max(0, total - processed)
                avg_time_per_msg = self.stats.elapsed_seconds / processed
                rem_seconds = int(remaining_msgs * avg_time_per_msg)
                eta_str = format_seconds(rem_seconds)
            else:
                eta_str = "Calculating..."

            elapsed = format_seconds(self.stats.elapsed_seconds)
            total_str = str(total) if total > 0 else "Detecting..."
            title = "📊 <b>Migration Status Update (Live)</b>" if not is_final else "🏁 <b>Migration Finished</b>"

            text = (
                f"{title}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📥 <b>Source:</b> {self.config.source_chat_title}\n"
                f"📤 <b>Destination:</b> {self.config.dest_chat_title}\n"
                f"📈 <b>Progress:</b> {progress_bar} ({self.stats.processed_count}/{total_str})\n"
                f"⚡ <b>Speed:</b> {speed_str}\n"
                f"⏳ <b>ETA:</b> ~{eta_str}\n"
                f"🎬 <b>Media Uploaded:</b> {self.stats.media_count}\n"
                f"📝 <b>Text Messages:</b> {self.stats.text_count}\n"
                f"⏭️ <b>Skipped:</b> {self.stats.skipped_count}\n"
                f"❌ <b>Errors:</b> {self.stats.failed_count}\n"
                f"⏱️ <b>Elapsed:</b> {elapsed}\n"
            )

            if not is_final and self.stats.current_msg_id:
                text += f"📍 <b>Current Message ID:</b> #{self.stats.current_msg_id}\n"

            if is_final:
                text += f"📌 <b>Final Status:</b> {self.stats.status.value}\n"
                if self.stats.error_message:
                    text += f"⚠️ <b>Error Details:</b> {self.stats.error_message}\n"

            # In-Place Live Message Editing
            if not self.progress_msg_id:
                sent_msg = await self._execute_with_flood_retry(
                    self.bot.send_message,
                    chat_id=self.owner_id,
                    text=text,
                    parse_mode=enums.ParseMode.HTML
                )
                if sent_msg:
                    self.progress_msg_id = sent_msg.id
            else:
                try:
                    await self._execute_with_flood_retry(
                        self.bot.edit_message_text,
                        chat_id=self.owner_id,
                        message_id=self.progress_msg_id,
                        text=text,
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    # If edit fails (e.g. user deleted the message), send a fresh live card
                    sent_msg = await self._execute_with_flood_retry(
                        self.bot.send_message,
                        chat_id=self.owner_id,
                        text=text,
                        parse_mode=enums.ParseMode.HTML
                    )
                    if sent_msg:
                        self.progress_msg_id = sent_msg.id
        except Exception as e:
            logger.warning(f"Failed to send progress notification: {e}")

    async def _forward_without_tag(self, dest_chat: Union[int, str], source_chat: Union[int, str], msg_id: int) -> bool:
        """
        Forwards a message using MTProto with drop_author=True.
        Completely strips the 'Forwarded from...' tag, making it appear as a 100% native post.
        """
        try:
            peer_to = await self._resolve_peer_cached(dest_chat)
            peer_from = await self._resolve_peer_cached(source_chat)

            await self.client.invoke(
                raw.functions.messages.ForwardMessages(
                    to_peer=peer_to,
                    from_peer=peer_from,
                    id=[msg_id],
                    random_id=[random.randint(1, 2**32)],
                    drop_author=True
                )
            )
            return True
        except FloodWait as fw:
            await asyncio.sleep(fw.value)
            return False
        except Exception as e:
            logger.debug(f"Forward without tag skipped for #{msg_id}: {e}")
            return False

    async def _try_instant_server_copy(self, msg: Message, dest_chat: Union[int, str]) -> bool:
        """
        Attempt instant zero-download server-side copy via Telegram MTProto file_id.
        Preserves 100% of Telegram Premium Custom Emojis, Entities, and Blockquotes.
        Returns True if successfully sent, False if restricted or unsupported.
        """
        try:
            caption, caption_entities = self._apply_caption(msg.caption, msg.caption_entities)

            if msg.video:
                await self._execute_with_flood_retry(
                    self.client.send_video,
                    chat_id=dest_chat,
                    video=msg.video.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                    duration=msg.video.duration or 0,
                    width=msg.video.width or 0,
                    height=msg.video.height or 0,
                    supports_streaming=True
                )
                self.stats.media_count += 1
                if msg.video.file_size:
                    self.stats.total_bytes_migrated += msg.video.file_size
                logger.info(f"⚡ [Instant Copy] Migrated video #{msg.id} instantly!")
                return True

            elif msg.document:
                await self._execute_with_flood_retry(
                    self.client.send_document,
                    chat_id=dest_chat,
                    document=msg.document.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                    file_name=msg.document.file_name
                )
                self.stats.media_count += 1
                if msg.document.file_size:
                    self.stats.total_bytes_migrated += msg.document.file_size
                logger.info(f"⚡ [Instant Copy] Migrated document #{msg.id} instantly!")
                return True

            elif msg.photo:
                await self._execute_with_flood_retry(
                    self.client.send_photo,
                    chat_id=dest_chat,
                    photo=msg.photo.file_id,
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                if msg.photo.file_size:
                    self.stats.total_bytes_migrated += msg.photo.file_size
                logger.info(f"⚡ [Instant Copy] Migrated photo #{msg.id} instantly!")
                return True

            elif msg.animation:
                await self._execute_with_flood_retry(
                    self.client.send_animation,
                    chat_id=dest_chat,
                    animation=msg.animation.file_id,
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                if msg.animation.file_size:
                    self.stats.total_bytes_migrated += msg.animation.file_size
                logger.info(f"⚡ [Instant Copy] Migrated GIF animation #{msg.id} instantly!")
                return True

            elif msg.sticker:
                await self._execute_with_flood_retry(
                    self.client.send_sticker,
                    chat_id=dest_chat,
                    sticker=msg.sticker.file_id
                )
                self.stats.media_count += 1
                logger.info(f"⚡ [Instant Copy] Migrated sticker #{msg.id} instantly!")
                return True

            elif msg.audio:
                await self._execute_with_flood_retry(
                    self.client.send_audio,
                    chat_id=dest_chat,
                    audio=msg.audio.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                    duration=msg.audio.duration or 0,
                    performer=msg.audio.performer,
                    title=msg.audio.title
                )
                self.stats.media_count += 1
                if msg.audio.file_size:
                    self.stats.total_bytes_migrated += msg.audio.file_size
                logger.info(f"⚡ [Instant Copy] Migrated audio #{msg.id} instantly!")
                return True

            elif msg.voice:
                await self._execute_with_flood_retry(
                    self.client.send_voice,
                    chat_id=dest_chat,
                    voice=msg.voice.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                    duration=msg.voice.duration or 0
                )
                self.stats.media_count += 1
                if msg.voice.file_size:
                    self.stats.total_bytes_migrated += msg.voice.file_size
                logger.info(f"⚡ [Instant Copy] Migrated voice #{msg.id} instantly!")
                return True

            elif msg.video_note:
                await self._execute_with_flood_retry(
                    self.client.send_video_note,
                    chat_id=dest_chat,
                    video_note=msg.video_note.file_id,
                    duration=msg.video_note.duration or 0,
                    length=msg.video_note.length or 0
                )
                self.stats.media_count += 1
                if msg.video_note.file_size:
                    self.stats.total_bytes_migrated += msg.video_note.file_size
                logger.info(f"⚡ [Instant Copy] Migrated video note #{msg.id} instantly!")
                return True

            elif msg.contact:
                await self._execute_with_flood_retry(
                    self.client.send_contact,
                    chat_id=dest_chat,
                    phone_number=msg.contact.phone_number,
                    first_name=msg.contact.first_name,
                    last_name=msg.contact.last_name or "",
                    vcard=msg.contact.vcard
                )
                self.stats.text_count += 1
                logger.info(f"⚡ [Instant Copy] Migrated contact #{msg.id} instantly!")
                return True

            elif msg.location:
                await self._execute_with_flood_retry(
                    self.client.send_location,
                    chat_id=dest_chat,
                    latitude=msg.location.latitude,
                    longitude=msg.location.longitude
                )
                self.stats.text_count += 1
                logger.info(f"⚡ [Instant Copy] Migrated location #{msg.id} instantly!")
                return True

            elif msg.dice:
                await self._execute_with_flood_retry(
                    self.client.send_dice,
                    chat_id=dest_chat,
                    emoji=msg.dice.emoji
                )
                self.stats.text_count += 1
                logger.info(f"⚡ [Instant Copy] Migrated dice #{msg.id} instantly!")
                return True

        except Exception as e:
            logger.info(f"Instant server copy skipped for #{msg.id} ({e}). Seamlessly switching to local stream...")
            return False

        return False

    async def _download_media_to_file(self, msg: Message) -> Optional[Path]:
        """Download media of a message to temporary file safely with valid extension."""
        if not msg or not msg.media:
            return None

        # Determine appropriate file extension based on media type
        if msg.photo:
            ext = ".jpg"
        elif msg.video:
            ext = ".mp4"
        elif msg.animation:
            ext = ".mp4"
        elif msg.audio:
            ext = ".mp3"
        elif msg.voice:
            ext = ".ogg"
        elif msg.video_note:
            ext = ".mp4"
        elif msg.sticker:
            ext = ".webp"
        elif msg.document and msg.document.file_name:
            _, doc_ext = os.path.splitext(msg.document.file_name)
            ext = doc_ext or ".bin"
        else:
            ext = ".dat"

        temp_target = Config.DOWNLOAD_DIR / f"media_{msg.chat.id}_{msg.id}{ext}"
        downloaded = await self._execute_with_flood_retry(
            self.client.download_media,
            message=msg,
            file_name=str(temp_target)
        )
        if downloaded and os.path.exists(downloaded):
            p = Path(downloaded)
            # Ensure the downloaded file has a valid photo extension for Telegram send_photo
            if msg.photo and p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                fixed_p = p.with_suffix(".jpg")
                try:
                    p.rename(fixed_p)
                    return fixed_p
                except Exception:
                    return p
            return p
        return None

    async def _upload_and_post_media(self, msg: Message, local_file_path: Path) -> None:
        """Process and upload a local media file to the destination channel in strict sequence, preserving premium entities."""
        dest_chat = self.config.dest_chat_id
        extra_temp_files: List[Path] = []
        caption, caption_entities = self._apply_caption(msg.caption, msg.caption_entities)

        try:
            file_bytes = local_file_path.stat().st_size
            self.stats.total_bytes_migrated += file_bytes
            upload_video_path = str(local_file_path)

            is_doc_video = bool(msg.document and msg.document.file_name and any(msg.document.file_name.lower().endswith(v_ext) for v_ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", ".m4v", ".3gp"]))

            if msg.video or (is_doc_video and self.config.output_format == OutputFormat.VIDEO):
                thumb_path = None

                # A0. Clean/Mask old watermark if enabled
                if self.config.clean_old_watermark:
                    cleaned_path = local_file_path.with_name(f"cleaned_{local_file_path.name}")
                    extra_temp_files.append(cleaned_path)
                    clean_res = await remove_or_mask_watermark(
                        input_path=Path(upload_video_path),
                        output_path=cleaned_path,
                        position=self.config.clean_wm_position,
                        style=self.config.clean_wm_style
                    )
                    if clean_res:
                        upload_video_path = clean_res

                # A. Apply anti-theft watermark if enabled
                if self.config.enable_watermark:
                    watermarked_path = local_file_path.with_name(f"wm_{local_file_path.name}")
                    extra_temp_files.append(watermarked_path)
                    upload_video_path = await apply_video_watermark(
                        input_path=Path(upload_video_path),
                        output_path=watermarked_path,
                        watermark_text=self.config.watermark_text,
                        mode=self.config.watermark_mode
                    )

                # B. Determine thumbnail (Branded Custom Thumbnail vs Original vs Strip Existing)
                if self.config.enable_custom_thumbnail and self.config.custom_thumbnail_path and os.path.exists(self.config.custom_thumbnail_path):
                    thumb_path = str(Path(self.config.custom_thumbnail_path).resolve())
                elif self.config.strip_existing_thumbnail:
                    # Strip/Remove existing creator's promo thumbnail: extract clean natural frame from video using FFmpeg
                    extracted_thumb = local_file_path.with_name(f"clean_thumb_{local_file_path.stem}.jpg")
                    extra_temp_files.append(extracted_thumb)
                    thumb_res = await extract_video_thumbnail(upload_video_path, extracted_thumb)
                    if thumb_res and os.path.exists(thumb_res):
                        thumb_path = str(thumb_res)
                    else:
                        thumb_path = None
                else:
                    # Download original Telegram thumbnail directly from source if present
                    thumbs = getattr(msg.video, 'thumbs', None) or getattr(msg.document, 'thumbs', None)
                    if thumbs:
                        try:
                            biggest_thumb = thumbs[-1]
                            thumb_file = await self._execute_with_flood_retry(
                                self.client.download_media,
                                message=biggest_thumb.file_id,
                                file_name=str(Config.DOWNLOAD_DIR / f"thumb_{msg.chat.id}_{msg.id}.jpg")
                            )
                            if thumb_file and os.path.exists(thumb_file):
                                thumb_path = str(thumb_file)
                                extra_temp_files.append(Path(thumb_path))
                        except Exception as th_err:
                            logger.debug(f"Could not download source thumbnail: {th_err}")

                safe_thumb = thumb_path if (thumb_path and os.path.exists(thumb_path)) else None

                # D. Upload based on OutputFormat (Streamable Video vs Document File)
                if self.config.output_format == OutputFormat.VIDEO:
                    # Ensure streamable MP4 container with faststart
                    if not upload_video_path.lower().endswith(".mp4"):
                        remuxed_video = local_file_path.with_name(f"stream_{local_file_path.stem}.mp4")
                        extra_temp_files.append(remuxed_video)
                        upload_video_path = await remux_to_streamable_mp4(upload_video_path, remuxed_video)

                    v_dur = getattr(msg.video, 'duration', 0) or 0
                    v_w = getattr(msg.video, 'width', 0) or 0
                    v_h = getattr(msg.video, 'height', 0) or 0

                    await self._execute_with_flood_retry(
                        self.client.send_video,
                        chat_id=dest_chat,
                        video=upload_video_path,
                        caption=caption,
                        caption_entities=caption_entities,
                        thumb=safe_thumb,
                        duration=v_dur,
                        width=v_w,
                        height=v_h,
                        supports_streaming=True
                    )
                    self.stats.media_count += 1
                    logger.info(f"✅ Migrated streamable video #{msg.id} -> Dest Channel")
                else:
                    # OutputFormat == OutputFormat.FILE (send as document)
                    doc_name = getattr(msg.document, 'file_name', None) or f"{local_file_path.stem}.mp4"
                    await self._execute_with_flood_retry(
                        self.client.send_document,
                        chat_id=dest_chat,
                        document=upload_video_path,
                        caption=caption,
                        caption_entities=caption_entities,
                        thumb=safe_thumb,
                        file_name=doc_name,
                        force_document=True
                    )
                    self.stats.media_count += 1
                    logger.info(f"✅ Migrated video as document file #{msg.id} -> Dest Channel")

            elif msg.document:
                # Check for ZIP archive auto-extraction
                is_zip = bool(msg.document.file_name and msg.document.file_name.lower().endswith(".zip"))
                if is_zip and self.config.auto_extract_zip:
                    extracted_ok = await self._process_and_extract_zip(msg, local_file_path)
                    if extracted_ok:
                        return

                await self._execute_with_flood_retry(
                    self.client.send_document,
                    chat_id=dest_chat,
                    document=str(local_file_path),
                    caption=caption,
                    caption_entities=caption_entities,
                    file_name=msg.document.file_name
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated document message #{msg.id} -> Dest Channel")

            elif msg.photo:
                await self._execute_with_flood_retry(
                    self.client.send_photo,
                    chat_id=dest_chat,
                    photo=str(local_file_path),
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated photo message #{msg.id} -> Dest Channel")

            elif msg.audio:
                await self._execute_with_flood_retry(
                    self.client.send_audio,
                    chat_id=dest_chat,
                    audio=str(local_file_path),
                    caption=caption,
                    caption_entities=caption_entities,
                    duration=msg.audio.duration or 0,
                    performer=msg.audio.performer,
                    title=msg.audio.title
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated audio message #{msg.id} -> Dest Channel")

            elif msg.voice:
                await self._execute_with_flood_retry(
                    self.client.send_voice,
                    chat_id=dest_chat,
                    voice=str(local_file_path),
                    caption=caption,
                    caption_entities=caption_entities,
                    duration=msg.voice.duration or 0
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated voice message #{msg.id} -> Dest Channel")

            elif msg.video_note:
                await self._execute_with_flood_retry(
                    self.client.send_video_note,
                    chat_id=dest_chat,
                    video_note=str(local_file_path),
                    duration=msg.video_note.duration or 0,
                    length=msg.video_note.length or 0
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated video note message #{msg.id} -> Dest Channel")

            elif msg.animation:
                await self._execute_with_flood_retry(
                    self.client.send_animation,
                    chat_id=dest_chat,
                    animation=str(local_file_path),
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated GIF animation message #{msg.id} -> Dest Channel")

            elif msg.sticker:
                await self._execute_with_flood_retry(
                    self.client.send_sticker,
                    chat_id=dest_chat,
                    sticker=str(local_file_path)
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated sticker message #{msg.id} -> Dest Channel")

        finally:
            # Clean up all temporary files immediately to conserve disk space
            cleanup_temp_file(local_file_path)
            for extra_p in extra_temp_files:
                cleanup_temp_file(extra_p)

    async def _process_and_extract_zip(self, msg: Message, local_zip_path: Path) -> bool:
        """
        Unpacks a zip archive and migrates each extracted file in natural sequential order.
        Videos are posted as video or document based on output_format, PDFs/notes as documents.
        If extraction fails or is password-protected, returns False so it uploads the zip as-is.
        """
        extract_dir = Config.DOWNLOAD_DIR / f"unzip_{msg.chat.id}_{msg.id}"
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(local_zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            extracted_files: List[Path] = []
            for root, _, files in os.walk(extract_dir):
                for f in files:
                    if not f.startswith(".") and not f.startswith("__MACOSX"):
                        extracted_files.append(Path(root) / f)

            if not extracted_files:
                return False

            # Natural alphanumeric sort for lecture sequence (01.mp4, 02.mp4, 10.mp4)
            extracted_files.sort(key=lambda p: natural_sort_key(p.name))
            logger.info(f"📦 Successfully unpacked ZIP #{msg.id}: {len(extracted_files)} files found in archive.")

            for sub_file in extracted_files:
                if self.cancel_event.is_set():
                    break
                await self._migrate_extracted_file(sub_file, original_msg=msg)

            return True

        except Exception as e:
            logger.warning(f"ZIP extraction failed for #{msg.id} ({e}). Uploading original ZIP file as fallback.")
            return False
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

    async def _migrate_extracted_file(self, file_path: Path, original_msg: Message) -> None:
        """Migrates a single extracted file from a ZIP archive."""
        dest_chat = self.config.dest_chat_id
        ext = file_path.suffix.lower()
        file_bytes = file_path.stat().st_size
        self.stats.total_bytes_migrated += file_bytes

        caption_text = file_path.name
        caption, caption_entities = self._apply_caption(caption_text, None)
        extra_temp_files: List[Path] = []

        try:
            # 1. Video files
            if ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", ".m4v", ".3gp"]:
                upload_video_path = str(file_path)

                if self.config.enable_watermark:
                    watermarked_path = file_path.with_name(f"wm_{file_path.name}")
                    extra_temp_files.append(watermarked_path)
                    upload_video_path = await apply_video_watermark(
                        input_path=file_path,
                        output_path=watermarked_path,
                        watermark_text=self.config.watermark_text,
                        mode=self.config.watermark_mode
                    )

                thumb_path = None
                if self.config.enable_custom_thumbnail and self.config.custom_thumbnail_path and os.path.exists(self.config.custom_thumbnail_path):
                    thumb_path = str(Path(self.config.custom_thumbnail_path).resolve())
                elif self.config.strip_existing_thumbnail:
                    extracted_thumb = file_path.with_name(f"thumb_{file_path.stem}.jpg")
                    extra_temp_files.append(extracted_thumb)
                    thumb_res = await extract_video_thumbnail(upload_video_path, extracted_thumb)
                    if thumb_res and os.path.exists(thumb_res):
                        thumb_path = str(thumb_res)

                safe_thumb = thumb_path if (thumb_path and os.path.exists(thumb_path)) else None

                if self.config.output_format == OutputFormat.VIDEO:
                    if not upload_video_path.lower().endswith(".mp4"):
                        remuxed = file_path.with_name(f"stream_{file_path.stem}.mp4")
                        extra_temp_files.append(remuxed)
                        upload_video_path = await remux_to_streamable_mp4(upload_video_path, remuxed)

                    await self._execute_with_flood_retry(
                        self.client.send_video,
                        chat_id=dest_chat,
                        video=upload_video_path,
                        caption=caption,
                        caption_entities=caption_entities,
                        thumb=safe_thumb,
                        supports_streaming=True
                    )
                else:
                    await self._execute_with_flood_retry(
                        self.client.send_document,
                        chat_id=dest_chat,
                        document=upload_video_path,
                        caption=caption,
                        caption_entities=caption_entities,
                        thumb=safe_thumb,
                        file_name=file_path.name,
                        force_document=True
                    )

                self.stats.media_count += 1
                logger.info(f"✅ Migrated unzipped video: {file_path.name}")

            # 2. Audio files
            elif ext in [".mp3", ".m4a", ".ogg", ".flac", ".wav", ".aac"]:
                await self._execute_with_flood_retry(
                    self.client.send_audio,
                    chat_id=dest_chat,
                    audio=str(file_path),
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated unzipped audio: {file_path.name}")

            # 3. Photo files
            elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
                await self._execute_with_flood_retry(
                    self.client.send_photo,
                    chat_id=dest_chat,
                    photo=str(file_path),
                    caption=caption,
                    caption_entities=caption_entities
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated unzipped image: {file_path.name}")

            # 4. Documents / PDFs / Other files
            else:
                await self._execute_with_flood_retry(
                    self.client.send_document,
                    chat_id=dest_chat,
                    document=str(file_path),
                    caption=caption,
                    caption_entities=caption_entities,
                    file_name=file_path.name,
                    force_document=True
                )
                self.stats.media_count += 1
                logger.info(f"✅ Migrated unzipped document: {file_path.name}")

        finally:
            for extra_p in extra_temp_files:
                cleanup_temp_file(extra_p)

    async def _migrate_single_message(self, msg: Message) -> None:
        """
        Migrates a single message of ANY format (Text, Links, Polls, GIFs, Stickers, Media, Contacts).
        Tries instant server copy first. If restricted/custom-thumb, downloads & uploads.
        Preserves 100% of Telegram Premium Custom Emojis, Entities, and Blockquotes.
        """
        dest_chat = self.config.dest_chat_id

        # 0. BOT_ADMIN Mode (Direct Bot Admin Transfer)
        if self.config.engine_type == EngineType.BOT_ADMIN:
            try:
                copied = await self._execute_with_flood_retry(
                    self.bot.copy_message,
                    chat_id=dest_chat,
                    from_chat_id=self.config.source_chat_id,
                    message_id=msg.id
                )
                if copied:
                    if msg.media:
                        self.stats.media_count += 1
                    else:
                        self.stats.text_count += 1
                    logger.info(f"🤖 [Bot Mode] Copied message #{msg.id} -> Dest Channel")
                    return
            except Exception as bot_err:
                logger.debug(f"Bot copy_message failed for #{msg.id}: {bot_err}. Falling back to bot forward...")
                try:
                    fwd = await self._execute_with_flood_retry(
                        self.bot.forward_messages,
                        chat_id=dest_chat,
                        from_chat_id=self.config.source_chat_id,
                        message_ids=[msg.id],
                        drop_author=True
                    )
                    if fwd:
                        self.stats.media_count += 1
                        logger.info(f"🤖 [Bot Mode] Forwarded message #{msg.id} -> Dest Channel")
                        return
                except Exception as fwd_err:
                    logger.warning(f"Bot Admin forward failed for #{msg.id}: {fwd_err}")

        # 1. Poll Messages
        if msg.poll:
            try:
                poll = msg.poll
                options = [opt.text for opt in poll.options] if poll.options else ["Yes", "No"]
                await self._execute_with_flood_retry(
                    self.client.send_poll,
                    chat_id=dest_chat,
                    question=poll.question,
                    options=options,
                    is_anonymous=poll.is_anonymous if poll.is_anonymous is not None else True,
                    type=poll.type if poll.type else enums.PollType.REGULAR,
                    allows_multiple_answers=poll.allows_multiple_answers if poll.allows_multiple_answers is not None else False,
                    correct_option_id=poll.correct_option_id,
                    explanation=poll.explanation
                )
                self.stats.text_count += 1
                logger.info(f"✅ Migrated poll message #{msg.id} -> Dest Channel")
            except Exception as poll_err:
                logger.error(f"Failed to migrate poll #{msg.id}: {poll_err}")
                self.stats.failed_count += 1
            return

        # 2. Text-only message OR WebPage Link Previews (Mega links, YouTube, URLs)
        has_media_file = bool(msg.photo or msg.video or msg.document or msg.audio or msg.voice or msg.video_note or msg.animation or msg.sticker)
        if not has_media_file:
            text_content = msg.text or msg.caption
            final_text, final_entities = self._apply_caption(text_content, msg.entities or msg.caption_entities)
            if final_text:
                await self._execute_with_flood_retry(
                    self.client.send_message,
                    chat_id=dest_chat,
                    text=final_text,
                    entities=final_entities,
                    disable_web_page_preview=False
                )
                self.stats.text_count += 1
                logger.debug(f"Migrated text/link message #{msg.id}")
            else:
                # Try smart forward for Telegram Premium modern layers (without 'Forwarded from' tag)
                try:
                    fwd_ok = await self._forward_without_tag(
                        dest_chat=dest_chat,
                        source_chat=self.config.source_chat_id,
                        msg_id=msg.id
                    )
                    if fwd_ok:
                        self.stats.media_count += 1
                        logger.info(f"⚡ [Smart Clean Forward] Migrated premium message #{msg.id} without forward tag!")
                        return
                except Exception:
                    self.stats.skipped_count += 1
            return

        # 3. Try Instant Server-Side Copy (Bypassed if video needs watermark/thumb/remux, or zip needs extraction)
        is_zip = bool(msg.document and msg.document.file_name and msg.document.file_name.lower().endswith(".zip"))
        is_doc_video = bool(msg.document and msg.document.file_name and any(msg.document.file_name.lower().endswith(v_ext) for v_ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", ".m4v", ".3gp"]))

        needs_video_mod = bool(
            (msg.video or is_doc_video) and (
                self.config.enable_custom_thumbnail or
                self.config.enable_watermark or
                self.config.clean_old_watermark or
                self.config.strip_existing_thumbnail or
                (msg.video and self.config.output_format == OutputFormat.FILE) or
                (is_doc_video and self.config.output_format == OutputFormat.VIDEO)
            )
        )
        if not needs_video_mod and not (is_zip and self.config.auto_extract_zip):
            if await self._try_instant_server_copy(msg, dest_chat):
                return

        # 4. Special non-downloadable objects (Dice, Contact, Location)
        if msg.dice or msg.contact or msg.location or msg.venue:
            logger.info(f"Special object #{msg.id} processed.")
            self.stats.skipped_count += 1
            return

        # 5. Media message via Download -> Parallel Upload (when restricted or video modification needed)
        logger.debug(f"Downloading media for message #{msg.id}...")
        local_path = await self._download_media_to_file(msg)

        if not local_path or not local_path.exists():
            logger.warning(f"Media download returned empty/missing file for message #{msg.id}")
            self.stats.failed_count += 1
            return

        await self._upload_and_post_media(msg, local_path)

    async def _run_migration_pipeline(self, progress_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
        """Internal worker executing the message download and upload loop."""
        logger.info(
            f"🚀 Starting migration pipeline. Source: {self.config.source_chat_id} -> "
            f"Dest: {self.config.dest_chat_id} (Mode: {self.config.mode.value})"
        )

        try:
            # Ensure both chats can be resolved by client
            try:
                source_peer = await self._execute_with_flood_retry(self.client.get_chat, self.config.source_chat_id)
            except Exception:
                try:
                    async for _ in self.client.get_dialogs(limit=50):
                        pass
                except Exception:
                    pass
                source_peer = await self._execute_with_flood_retry(self.client.get_chat, self.config.source_chat_id)

            try:
                dest_peer = await self._execute_with_flood_retry(self.client.get_chat, self.config.dest_chat_id)
            except Exception:
                try:
                    async for _ in self.client.get_dialogs(limit=50):
                        pass
                except Exception:
                    pass
                dest_peer = await self._execute_with_flood_retry(self.client.get_chat, self.config.dest_chat_id)

            self.config.source_chat_title = source_peer.title or str(source_peer.id)
            self.config.dest_chat_title = dest_peer.title or str(dest_peer.id)

            # Pre-cache resolved MTProto raw peers for rapid forwarding
            try:
                await self._resolve_peer_cached(self.config.source_chat_id)
                await self._resolve_peer_cached(self.config.dest_chat_id)
            except Exception:
                pass

            # Check for existing checkpoint for auto-resume
            checkpoint_id = load_checkpoint(self.config.source_chat_id, self.config.dest_chat_id)

            # Determine message IDs to process
            if self.config.mode == MigrationMode.RANGE:
                start_id = self.config.start_msg_id
                end_id = self.config.end_msg_id
                msg_ids = list(range(start_id, end_id + 1))
                self.stats.total_messages = len(msg_ids)
                logger.info(f"Processing range of {len(msg_ids)} message IDs (#{start_id} to #{end_id}).")
            else:
                # Full channel mode: Detect max message ID
                logger.info("Detecting total message count for Full Channel migration...")
                latest_msg = None
                async for m in self.client.get_chat_history(self.config.source_chat_id, limit=1):
                    latest_msg = m
                    break

                if not latest_msg:
                    raise ValueError("Source channel appears to be empty or inaccessible.")

                max_id = latest_msg.id
                effective_start = 1

                if checkpoint_id > 0 and checkpoint_id < max_id:
                    effective_start = checkpoint_id + 1
                    logger.info(f"🔄 Auto-Resuming from checkpoint message #{effective_start} (out of #{max_id}).")
                    try:
                        await self.bot.send_message(
                            chat_id=self.owner_id,
                            text=(
                                f"🔄 <b>Auto-Resume Activated</b>\n\n"
                                f"Resuming migration from Checkpoint: <b>Message #{effective_start}</b> "
                                f"(Total: #{max_id})."
                            ),
                            parse_mode=enums.ParseMode.HTML
                        )
                    except Exception:
                        pass

                msg_ids = list(range(effective_start, max_id + 1))
                self.stats.total_messages = len(msg_ids)
                logger.info(f"Starting streaming migration of {len(msg_ids)} message IDs (from #{effective_start} to #{max_id}).")

            # Send immediate initial progress update
            await self._send_progress_update(is_final=False)

            # Streaming batch size (50 IDs per roundtrip for optimal throughput)
            chunk_size = 50
            last_progress_count = 0

            for i in range(0, len(msg_ids), chunk_size):
                if self.cancel_event.is_set():
                    logger.info("Migration cancelled by user signal.")
                    self.stats.status = JobStatus.CANCELLED
                    break

                batch_ids = msg_ids[i:i + chunk_size]
                batch_msgs = await self._execute_with_flood_retry(
                    self.client.get_messages,
                    chat_id=self.config.source_chat_id,
                    message_ids=batch_ids
                )

                if not isinstance(batch_msgs, list):
                    batch_msgs = [batch_msgs] if batch_msgs else []

                for idx, msg in enumerate(batch_msgs):
                    if self.cancel_event.is_set():
                        break

                    if not msg or msg.empty or msg.service:
                        msg_target_id = msg.id if msg else batch_ids[idx]
                        reason = "Deleted / Empty Message" if (not msg or msg.empty) else "Service Message"
                        logger.info(f"⏭️ Skipped message #{msg_target_id}: {reason}")
                        self.stats.skipped_count += 1
                        self.stats.processed_count += 1
                        save_checkpoint(self.config.source_chat_id, self.config.dest_chat_id, msg_target_id)
                        continue

                    self.stats.current_msg_id = msg.id

                    try:
                        await self._migrate_single_message(msg)
                        self.stats.processed_count += 1
                        # Save checkpoint on successful progress
                        save_checkpoint(self.config.source_chat_id, self.config.dest_chat_id, msg.id)
                    except Exception as msg_err:
                        self.stats.failed_count += 1
                        self.stats.processed_count += 1
                        save_checkpoint(self.config.source_chat_id, self.config.dest_chat_id, msg.id)
                        logger.error(f"❌ Error migrating message #{msg.id}: {msg_err}", exc_info=True)

                    # Send progress update every PROGRESS_INTERVAL messages
                    if (self.stats.processed_count - last_progress_count) >= Config.PROGRESS_INTERVAL:
                        last_progress_count = self.stats.processed_count
                        await self._send_progress_update(is_final=False)

                    # Golden Cruise Pacing (1.15s - 1.25s for instant copy = ~3000 msgs/hr with 0 floodwaits)
                    is_instant = not self.config.enable_custom_thumbnail and not self.config.enable_watermark
                    delay = random.uniform(1.15, 1.25) if is_instant else 0.5
                    await asyncio.sleep(delay)

            if not self.cancel_event.is_set() and self.stats.status == JobStatus.RUNNING:
                self.stats.status = JobStatus.COMPLETED

        except asyncio.CancelledError:
            self.stats.status = JobStatus.CANCELLED
            logger.info("Migration task was cancelled.")
        except Exception as e:
            self.stats.status = JobStatus.FAILED
            self.stats.error_message = str(e)
            logger.error(f"❌ Fatal error during migration: {e}", exc_info=True)
        finally:
            self.stats.end_time = time.time()
            logger.info(
                f"🏁 Migration finished with status '{self.stats.status.value}'. "
                f"Processed: {self.stats.processed_count}, Media: {self.stats.media_count}, "
                f"Text: {self.stats.text_count}, Skipped: {self.stats.skipped_count}, "
                f"Failed: {self.stats.failed_count} in {format_seconds(self.stats.elapsed_seconds)}."
            )
            # Send final report
            await self._send_progress_update(is_final=True)

    def cancel_deletion(self) -> bool:
        """Signals the running deletion job to cancel."""
        if not self.is_deleting():
            return False
        logger.info("Deletion cancellation requested by user.")
        self.deletion_cancel_event.set()
        return True

    async def start_deletion(self) -> None:
        """Initiates the deletion job asynchronously."""
        if self.is_busy():
            raise RuntimeError("A migration or deletion job is already running.")

        if not self.deletion_target_chat:
            raise ValueError("Target channel must be selected before starting deletion.")

        if self.config.engine_type == EngineType.USERBOT:
            from client import get_or_create_user_client
            self.userbot = await get_or_create_user_client(self.owner_id)
            if not self.userbot:
                raise ValueError("⚠️ Userbot session is not active! Please log in using /settings.")

        self.deletion_cancel_event.clear()
        self.deletion_progress_msg_id = None
        self.deletion_stats = DeletionStats(status=JobStatus.RUNNING, start_time=time.time())

        self._deletion_task = asyncio.create_task(self._run_deletion_pipeline())

    async def _send_deletion_progress_update(self, is_final: bool = False) -> None:
        """Sends or edits live deletion progress message."""
        stats = self.deletion_stats
        target_display = self.deletion_target_title or str(self.deletion_target_chat)
        if stats.status == JobStatus.RUNNING:
            header = "🗑️ <b>Channel Deletion in Progress...</b>"
            pbar = format_progress_bar(stats.deleted_count, stats.total_messages)
            status_line = f"🟢 <b>RUNNING</b> ({stats.deleted_count}/{stats.total_messages})"
        elif stats.status == JobStatus.COMPLETED:
            header = "🏁 <b>Channel Deletion Completed!</b>"
            pbar = format_progress_bar(stats.total_messages, stats.total_messages)
            status_line = "✅ <b>COMPLETED</b>"
        elif stats.status == JobStatus.CANCELLED:
            header = "⏹️ <b>Channel Deletion Cancelled</b>"
            pbar = format_progress_bar(stats.deleted_count, stats.total_messages)
            status_line = "⏹️ <b>CANCELLED</b>"
        else:
            header = "❌ <b>Channel Deletion Failed</b>"
            pbar = format_progress_bar(stats.deleted_count, stats.total_messages)
            status_line = f"❌ <b>FAILED:</b> {stats.error_message}"

        text = (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Target Channel:</b> {target_display}\n"
            f"📊 <b>Progress:</b> {pbar}\n"
            f"🗑️ <b>Deleted:</b> <b>{stats.deleted_count}</b> messages\n"
            f"❌ <b>Errors:</b> <b>{stats.failed_count}</b>\n"
            f"⏱️ <b>Elapsed:</b> {format_seconds(stats.elapsed_seconds)}\n"
            f"📌 <b>Status:</b> {status_line}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            if not self.deletion_progress_msg_id or is_final:
                sent = await self._execute_with_flood_retry(
                    self.bot.send_message,
                    chat_id=self.owner_id,
                    text=text,
                    parse_mode=enums.ParseMode.HTML
                )
                if sent and not is_final:
                    self.deletion_progress_msg_id = sent.id
            else:
                try:
                    await self._execute_with_flood_retry(
                        self.bot.edit_message_text,
                        chat_id=self.owner_id,
                        message_id=self.deletion_progress_msg_id,
                        text=text,
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Deletion progress notice error: {e}")

    async def _run_deletion_pipeline(self) -> None:
        """High-speed batch deletion worker."""
        logger.info(f"🗑️ Starting deletion pipeline on {self.deletion_target_chat}")
        target_chat = self.deletion_target_chat

        try:
            # 1. Check Admin Rights
            try:
                member = await self._execute_with_flood_retry(self.client.get_chat_member, target_chat, "me")
                if self.config.engine_type == EngineType.BOT_ADMIN:
                    privs = getattr(member, "privileges", None)
                    if not (privs and getattr(privs, "can_delete_messages", False)):
                        raise PermissionError("⚠️ @CV_AUTOFORWARD_bot is not an Administrator with 'Delete Messages' permission in this channel!")
                else:
                    if member.status not in [enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR]:
                        raise PermissionError("⚠️ Your account is not an Administrator in this channel!")
            except Exception as perm_err:
                raise PermissionError(f"Admin Verification Failed: {perm_err}")

            # 2. Determine message IDs to delete
            if self.deletion_mode == MigrationMode.RANGE:
                start_id = min(self.deletion_start_msg_id or 1, self.deletion_end_msg_id or 1)
                end_id = max(self.deletion_start_msg_id or 1, self.deletion_end_msg_id or 1)
                msg_ids = list(range(start_id, end_id + 1))
            else:
                latest_msg = None
                async for m in self.client.get_chat_history(target_chat, limit=1):
                    latest_msg = m
                    break
                if not latest_msg:
                    raise ValueError("Target channel appears to be empty or inaccessible.")
                msg_ids = list(range(1, latest_msg.id + 1))

            self.deletion_stats.total_messages = len(msg_ids)
            await self._send_deletion_progress_update(is_final=False)

            # High-speed batch deletion (100 messages per call in Pyrogram)
            chunk_size = 100
            for i in range(0, len(msg_ids), chunk_size):
                if self.deletion_cancel_event.is_set():
                    self.deletion_stats.status = JobStatus.CANCELLED
                    break

                batch_ids = msg_ids[i:i + chunk_size]
                try:
                    await self._execute_with_flood_retry(
                        self.client.delete_messages,
                        chat_id=target_chat,
                        message_ids=batch_ids,
                        revoke=True
                    )
                    self.deletion_stats.deleted_count += len(batch_ids)
                except Exception as del_err:
                    logger.warning(f"Batch delete error: {del_err}")
                    self.deletion_stats.failed_count += len(batch_ids)

                await self._send_deletion_progress_update(is_final=False)
                await asyncio.sleep(0.2)

            if not self.deletion_cancel_event.is_set() and self.deletion_stats.status == JobStatus.RUNNING:
                self.deletion_stats.status = JobStatus.COMPLETED

        except asyncio.CancelledError:
            self.deletion_stats.status = JobStatus.CANCELLED
        except Exception as e:
            self.deletion_stats.status = JobStatus.FAILED
            self.deletion_stats.error_message = str(e)
            logger.error(f"❌ Deletion failed: {e}", exc_info=True)
        finally:
            self.deletion_stats.end_time = time.time()
            await self._send_deletion_progress_update(is_final=True)



# Multi-user migration engines cache: {user_id: MigrationEngine}
USER_ENGINES: Dict[int, MigrationEngine] = {}


def get_user_engine(user_id: int, bot: Client, userbot: Optional[Client] = None) -> MigrationEngine:
    """Get or create an isolated MigrationEngine instance for the specific user ID."""
    global USER_ENGINES
    if user_id not in USER_ENGINES:
        USER_ENGINES[user_id] = MigrationEngine(userbot=userbot, bot=bot, owner_id=user_id)
    elif userbot and not USER_ENGINES[user_id].userbot:
        USER_ENGINES[user_id].userbot = userbot
    return USER_ENGINES[user_id]

