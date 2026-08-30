"""
Bot command and inline callback handlers.
Provides multi-user isolated control interface via Telegram inline keyboard buttons,
per-user channel pickers, thumbnail/watermark/caption submenus, dual-mode engine selector (Bots Hub),
Action Hubs (Forwarding vs Channel Deletion), Super Admin Panel, and Comparison Guide.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Dict, Optional, Union, Any

from pyrogram import Client, filters, enums
from pyrogram.errors import MessageNotModified, UserNotParticipant, ChatAdminRequired
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from config import Config
from utils import (
    parse_telegram_link,
    parse_message_id_or_link,
    format_chat_display,
    format_seconds,
    format_progress_bar
)
from client import (
    get_user_cached_channels,
    sync_user_dialogs,
    get_user_profile,
    is_user_logged_in,
    send_user_login_code,
    complete_user_login,
    logout_user
)
from migration import (
    OutputFormat,
    MigrationEngine,
    MigrationMode,
    CaptionMode,
    JobStatus,
    EngineType,
    get_user_engine,
    load_checkpoint,
    reset_checkpoint
)
import database

logger = logging.getLogger("migration_bot.handlers")

# User conversational state storage: {user_id: {"state": str, ...}}
USER_STATES: Dict[int, Dict[str, Any]] = {}

# Conversational state constants
STATE_NONE = "NONE"
STATE_WAITING_START_LINK = "WAITING_START_LINK"
STATE_WAITING_END_LINK = "WAITING_END_LINK"
STATE_WAITING_CUSTOM_SRC = "WAITING_CUSTOM_SRC"
STATE_WAITING_CUSTOM_DST = "WAITING_CUSTOM_DST"
STATE_WAITING_BOT_SRC = "WAITING_BOT_SRC"
STATE_WAITING_BOT_DST = "WAITING_BOT_DST"
STATE_WAITING_BOT_DEL_CH = "WAITING_BOT_DEL_CH"
STATE_WAITING_DEL_START = "WAITING_DEL_START"
STATE_WAITING_DEL_END = "WAITING_DEL_END"
STATE_WAITING_THUMB_PHOTO = "WAITING_THUMB_PHOTO"
STATE_WAITING_WM_TEXT = "WAITING_WM_TEXT"
STATE_WAITING_CAPTION_APPEND = "WAITING_CAPTION_APPEND"
STATE_WAITING_CAPTION_REPLACE = "WAITING_CAPTION_REPLACE"
STATE_WAITING_PHONE = "WAITING_PHONE"
STATE_WAITING_CODE = "WAITING_CODE"
STATE_WAITING_2FA = "WAITING_2FA"


def is_admin(user_id: int) -> bool:
    """Helper to check if user is the primary super admin."""
    return user_id == Config.OWNER_ID


def get_user_thumb_path(user_id: int) -> Path:
    """Returns isolated thumbnail path for user."""
    if user_id == Config.OWNER_ID and (Config.BASE_DIR / "thumb.jpg").exists():
        return Config.BASE_DIR / "thumb.jpg"
    return Config.BASE_DIR / f"thumb_{user_id}.jpg"


def track_user(user: Any) -> None:
    """Records/updates user metadata in local database."""
    if not user:
        return
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    database.save_or_update_user(
        user_id=user.id,
        name=full_name or "Telegram User",
        username=user.username
    )


# ----------------------------------------------------------------------
# 1. Main Welcome Screen
# ----------------------------------------------------------------------
def build_welcome_text() -> str:
    """Generates the main welcome hub message with official creator credit."""
    return (
        "👑 <b>CourseVerse™ Auto-Forward & Content Migration Hub</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <i>Proudly Created & Maintained by</i> <b>@CourseVerseHere</b>\n\n"
        "I am an <b>Enterprise-Grade Telegram Content Management Bot</b>.\n"
        "I can transfer high-volume video courses, documents, quizzes, and banners "
        "across channels with <b>100% Telegram Premium styling & Zero Forward Tags</b>, "
        "plus high-speed <b>Bulk Channel Deletion & Cleaning</b>!\n\n"
        "⚡ <b>Key Capabilities:</b>\n"
        "• <b>Dual Engine:</b> 👤 Userbot Mode & 🤖 Bot Mode\n"
        "• <b>Content Migration:</b> Zero Forward Tag (`drop_author=True`) & Premium Entities\n"
        "• <b>High-Speed Deletion:</b> Fast 100 msgs/batch Channel Cleaner\n"
        "• <b>Custom Branding:</b> Thumbnails, Watermarks, and Custom Caption Manager\n"
        "• <b>Multi-User Isolation:</b> 100% Private, individual sessions\n\n"
        "<i>Select an option below to proceed:</i>"
    )


def build_welcome_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Main welcome screen buttons."""
    buttons = [
        [
            InlineKeyboardButton("📢 Updates Channel", url=Config.UPDATES_CHANNEL_URL),
            InlineKeyboardButton("💬 Support Group", url=Config.SUPPORT_GROUP_URL)
        ],
        [
            InlineKeyboardButton("🤖 Bots (Select Migration / Deletion Engine)", callback_data="nav_bots_hub")
        ],
        [
            InlineKeyboardButton("👤 Userbot Account Manager", callback_data="nav_account"),
            InlineKeyboardButton("📖 Help & Guides", callback_data="nav_help")
        ]
    ]

    # Owner-only Super Admin Button
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("👑 Super Admin Panel", callback_data="nav_admin_panel")])

    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 2. `[🤖 Bots]` Submenu Screen
# ----------------------------------------------------------------------
def build_bots_hub_text() -> str:
    """Bots selector hub text."""
    return (
        "🤖 <b>Select Migration / Action Engine</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Choose which engine you want to use:\n\n"
        "🌟 <b>1. 👤 Userbot Mode (Recommended):</b>\n"
        "• Bypasses restricted & copy-protected channels 100%.\n"
        "• Supports all private & invite-only channels.\n"
        "• 100% Telegram Premium animated emojis & quotes preserved.\n"
        "• Custom HD thumbnails, moving watermarks & custom captions.\n"
        "• Channel message deletion (Requires User as Admin).\n\n"
        "⚡ <b>2. 🤖 Bot Mode:</b>\n"
        "• Uses Bot Token directly (Requires Bot as Admin in channels).\n"
        "• Zero phone login required (ideal for open channels).\n\n"
        "<i>Tap an option below to continue:</i>"
    )


def build_bots_hub_keyboard() -> InlineKeyboardMarkup:
    """Bots selector buttons."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Userbot Mode (Recommended)", callback_data="select_engine_userbot")],
        [InlineKeyboardButton("🤖 Bot Mode (Bot as Channel Admin)", callback_data="select_engine_bot")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
    ])


# ----------------------------------------------------------------------
# 3. Engine Action Hub (Forwarding vs Deletion)
# ----------------------------------------------------------------------
def build_engine_action_hub_text(engine: MigrationEngine, user_id: int) -> str:
    """Action selector text for the selected engine."""
    engine_name = "👤 Userbot Mode" if engine.config.engine_type == EngineType.USERBOT else "🤖 Bot Admin Mode"
    return (
        f"⚡ <b>{engine_name} — Action Hub</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select what task you want to perform:\n\n"
        "🚀 <b>1. Forward & Migrate Content:</b>\n"
        "Transfer channels, courses, quizzes & files with Zero Forward Tag.\n\n"
        "🗑️ <b>2. Delete Channel Content:</b>\n"
        "Bulk clean/delete messages from a channel (Range or Full).\n\n"
        "<i>Tap an option below to open its control panel:</i>"
    )


def build_engine_action_hub_keyboard() -> InlineKeyboardMarkup:
    """Buttons for selecting Forwarding or Deletion."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Forward / Migrate Content", callback_data="nav_forward_dash")],
        [InlineKeyboardButton("🗑️ Delete Channel Content", callback_data="nav_delete_dash")],
        [InlineKeyboardButton("⬅️ Back to Bots Menu", callback_data="nav_bots_hub")]
    ])


# ----------------------------------------------------------------------
# 4. Forwarding Dashboard
# ----------------------------------------------------------------------
async def build_forward_dashboard_text(engine: MigrationEngine, user_id: int) -> str:
    """Generate Forwarding Dashboard summary with configuration and live status."""
    cfg = engine.config
    stats = engine.stats

    src_display = cfg.source_chat_title or "<i>Not selected</i>"
    dst_display = cfg.dest_chat_title or "<i>Not selected</i>"

    checkpoint_id = load_checkpoint(cfg.source_chat_id, cfg.dest_chat_id) if (cfg.source_chat_id and cfg.dest_chat_id) else 0

    if cfg.mode == MigrationMode.FULL:
        if checkpoint_id > 0:
            mode_display = f"🔄 <b>Full Channel Mode</b> (Auto-Resuming from #{checkpoint_id + 1})"
        else:
            mode_display = "🔄 <b>Full Channel Mode</b> (All messages from start)"
    else:
        if cfg.start_msg_id and cfg.end_msg_id:
            count = (cfg.end_msg_id - cfg.start_msg_id + 1)
            mode_display = f"🔢 <b>Range Mode:</b> #{cfg.start_msg_id} ➔ #{cfg.end_msg_id} ({count} msgs)"
        else:
            mode_display = "🔢 <b>Range Mode:</b> <i>Not configured</i>"

    if cfg.engine_type == EngineType.USERBOT:
        user_logged = await is_user_logged_in(user_id)
        if user_logged:
            acc = await get_user_profile(user_id)
            engine_str = f"👤 <b>Userbot Mode</b> (✅ {acc.get('name')})"
        else:
            engine_str = "👤 <b>Userbot Mode</b> (⚠️ <i>No Userbot — Login in /settings</i>)"
    else:
        engine_str = "🤖 <b>Bot Admin Mode</b> (Zero account risk)"

    thumb_path = get_user_thumb_path(user_id)
    if cfg.enable_custom_thumbnail and thumb_path.exists():
        thumb_status = f"✅ <b>ON</b> (<code>{thumb_path.name}</code>)"
    elif cfg.enable_custom_thumbnail:
        thumb_status = "✅ <b>ON</b> (Default HD Cover)"
    else:
        thumb_status = "❌ <b>OFF</b> (Original Frame)"

    if cfg.enable_watermark:
        wm_status = f"✅ <b>ON</b> (<code>{cfg.watermark_text}</code> • {cfg.watermark_mode.capitalize()})"
    else:
        wm_status = "❌ <b>OFF</b> (Disabled)"

    # Caption status
    if cfg.caption_mode == CaptionMode.APPEND:
        caption_status = f"✅ <b>ADD / APPEND</b> (<code>{cfg.custom_caption_text[:20]}...</code>)"
    elif cfg.caption_mode == CaptionMode.REPLACE:
        caption_status = f"🔄 <b>REPLACE</b> (<code>{cfg.custom_caption_text[:20]}...</code>)"
    elif cfg.caption_mode == CaptionMode.REMOVE:
        caption_status = "🗑️ <b>REMOVE ALL CAPTIONS</b>"
    else:
        caption_status = "❌ <b>OFF (Original Captions)</b>"

    # Status indicator
    if stats.status == JobStatus.RUNNING:
        pbar = format_progress_bar(stats.processed_count, stats.total_messages)
        speed_str = f"⚡ {stats.speed_mbps:.1f} MB/s" if stats.speed_mbps > 0 else "⚡ Speed: Active"
        status_text = (
            f"🟢 <b>RUNNING</b>\n"
            f"   Progress: {pbar} ({stats.processed_count}/{stats.total_messages})\n"
            f"   {speed_str}\n"
            f"   Media: {stats.media_count} | Text: {stats.text_count} | Errors: {stats.failed_count}\n"
            f"   Elapsed: {format_seconds(stats.elapsed_seconds)}"
        )
    elif stats.status == JobStatus.COMPLETED:
        status_text = f"✅ <b>COMPLETED</b> (Migrated: {stats.media_count + stats.text_count}, Skipped: {stats.skipped_count})"
    elif stats.status == JobStatus.CANCELLED:
        status_text = f"⏹️ <b>CANCELLED</b> (Processed: {stats.processed_count}/{stats.total_messages})"
    elif stats.status == JobStatus.FAILED:
        status_text = f"❌ <b>FAILED</b> ({stats.error_message or 'Check logs'})"
    else:
        status_text = "⚪ <b>IDLE</b> (Ready)"

    checkpoint_info = f"📍 <b>Auto-Resume Checkpoint:</b> Message #{checkpoint_id}\n\n" if checkpoint_id > 0 else ""

    if cfg.output_format == OutputFormat.VIDEO:
        fmt_desc = "🎬 <b>Streamable Video (MP4 FastStart)</b>"
    elif cfg.output_format == OutputFormat.FILE:
        fmt_desc = "📁 <b>Document File (.mp4 Attachment)</b>"
    else:
        fmt_desc = "🔄 <b>As-Is (Original 1:1 Match)</b>"

    text = (
        "🚀 <b>Forwarding & Migration Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 <b>Active Engine:</b> {engine_str}\n\n"
        f"📥 <b>Incoming Channel (Source):</b>\n   {src_display}\n\n"
        f"📤 <b>Outgoing Channel (Destination):</b>\n   {dst_display}\n\n"
        f"⚙️ <b>Migration Mode:</b>\n   {mode_display}\n\n"
        f"🎞️ <b>Output Format:</b>\n   {fmt_desc}\n\n"
        f"📦 <b>Auto-Unpack ZIP Archives:</b>\n   {'✅ <b>ON (Auto-extract videos/PDFs)</b>' if cfg.auto_extract_zip else '❌ <b>OFF (Forward as .zip)</b>'}\n\n"
        f"{checkpoint_info}"
        f"🖼️ <b>Custom Thumbnail:</b>\n   {thumb_status}\n\n"
        f"🛡️ <b>Anti-Theft Watermark:</b>\n   {wm_status}\n\n"
        f"📝 <b>Custom Caption:</b>\n   {caption_status}\n\n"
        f"📊 <b>Job Status:</b>\n   {status_text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Use the buttons below to configure and run your migration:</i>"
    )
    return text


def build_forward_dashboard_keyboard(engine: MigrationEngine) -> InlineKeyboardMarkup:
    """Buttons for Forwarding Dashboard."""
    is_running = engine.is_busy()
    mode_toggle_label = "Switch to Full Mode 🔄" if engine.config.mode == MigrationMode.RANGE else "Switch to Range Mode 🔢"
    if engine.config.output_format == OutputFormat.VIDEO:
        fmt_btn_label = "🎞️ Format: 🎬 Video"
    elif engine.config.output_format == OutputFormat.FILE:
        fmt_btn_label = "🎞️ Format: 📁 Document"
    else:
        fmt_btn_label = "🎞️ Format: 🔄 As-Is"
    unzip_btn_label = "📦 Unzip: ✅ ON" if engine.config.auto_extract_zip else "📦 Unzip: ❌ OFF"

    buttons = [
        [
            InlineKeyboardButton("📥 Set Incoming", callback_data="menu_set_src"),
            InlineKeyboardButton("📤 Set Outgoing", callback_data="menu_set_dst")
        ],
        [
            InlineKeyboardButton("🔢 Set Message Range", callback_data="menu_set_range"),
            InlineKeyboardButton(mode_toggle_label, callback_data="menu_toggle_mode")
        ],
        [
            InlineKeyboardButton(fmt_btn_label, callback_data="opt_toggle_format"),
            InlineKeyboardButton(unzip_btn_label, callback_data="opt_toggle_unzip")
        ],
        [
            InlineKeyboardButton("🖼️ Thumbnail", callback_data="sub_thumbnail"),
            InlineKeyboardButton("🛡️ Watermark", callback_data="sub_watermark"),
            InlineKeyboardButton("📝 Caption", callback_data="sub_caption")
        ],
        [
            InlineKeyboardButton("🚀 RUN MIGRATION", callback_data="action_run"),
            InlineKeyboardButton("⏹️ STOP", callback_data="action_stop")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Status", callback_data="action_refresh_forward"),
            InlineKeyboardButton("⬅️ Back to Action Hub", callback_data="nav_engine_actions")
        ]
    ]

    checkpoint_id = load_checkpoint(engine.config.source_chat_id, engine.config.dest_chat_id) if (engine.config.source_chat_id and engine.config.dest_chat_id) else 0
    if checkpoint_id > 0 and not is_running:
        buttons.insert(4, [InlineKeyboardButton("♻️ Reset Progress (Start from #1)", callback_data="action_reset_cp")])

    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 5. Channel Deletion Dashboard
# ----------------------------------------------------------------------
def build_deletion_dashboard_text(engine: MigrationEngine, user_id: int) -> str:
    """Generate Deletion Dashboard summary."""
    stats = engine.deletion_stats
    target_display = engine.deletion_target_title or "<i>Not selected</i>"

    if engine.deletion_mode == MigrationMode.FULL:
        mode_display = "🔄 <b>Full Channel Cleaning (All Messages)</b>"
    else:
        if engine.deletion_start_msg_id and engine.deletion_end_msg_id:
            count = abs(engine.deletion_end_msg_id - engine.deletion_start_msg_id) + 1
            mode_display = f"🔢 <b>Range Mode:</b> #{engine.deletion_start_msg_id} ➔ #{engine.deletion_end_msg_id} ({count} msgs)"
        else:
            mode_display = "🔢 <b>Range Mode:</b> <i>Not configured</i>"

    engine_name = "👤 Userbot Mode" if engine.config.engine_type == EngineType.USERBOT else "🤖 Bot Admin Mode"

    if stats.status == JobStatus.RUNNING:
        pbar = format_progress_bar(stats.deleted_count, stats.total_messages)
        status_text = f"🟢 <b>DELETING...</b> ({pbar} {stats.deleted_count}/{stats.total_messages})"
    elif stats.status == JobStatus.COMPLETED:
        status_text = f"✅ <b>COMPLETED</b> (Deleted: {stats.deleted_count} messages)"
    elif stats.status == JobStatus.CANCELLED:
        status_text = f"⏹️ <b>CANCELLED</b> (Deleted: {stats.deleted_count}/{stats.total_messages})"
    elif stats.status == JobStatus.FAILED:
        status_text = f"❌ <b>FAILED:</b> {stats.error_message or 'Check permissions'}"
    else:
        status_text = "⚪ <b>IDLE (Ready)</b>"

    return (
        "🗑️ <b>Channel Message Deletion & Cleaner</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔌 <b>Active Engine:</b> {engine_name}\n\n"
        f"🎯 <b>Target Channel:</b> {target_display}\n\n"
        f"⚙️ <b>Deletion Scope:</b> {mode_display}\n\n"
        f"📊 <b>Deletion Status:</b> {status_text}\n\n"
        "⚠️ <b>CRITICAL REQUIREMENTS:</b>\n"
        "• <b>Userbot Mode:</b> Your user account MUST be an <b>Admin / Owner</b> with delete rights in the target channel.\n"
        "• <b>Bot Mode:</b> <b>@CV_AUTOFORWARD_bot</b> MUST be an <b>Admin</b> with <b>Delete Messages</b> permission.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Use the buttons below to configure and execute deletion:</i>"
    )


def build_deletion_dashboard_keyboard(engine: MigrationEngine) -> InlineKeyboardMarkup:
    """Buttons for Deletion Dashboard."""
    mode_toggle_label = "Switch to Full Mode 🔄" if engine.deletion_mode == MigrationMode.RANGE else "Switch to Range Mode 🔢"

    buttons = [
        [InlineKeyboardButton("🎯 Select Channel to Clean", callback_data="del_menu_ch")],
        [
            InlineKeyboardButton("🔢 Set Message Range", callback_data="del_menu_range"),
            InlineKeyboardButton(mode_toggle_label, callback_data="del_menu_toggle_mode")
        ],
        [
            InlineKeyboardButton("🗑️ START DELETION", callback_data="del_action_run"),
            InlineKeyboardButton("⏹️ STOP", callback_data="del_action_stop")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Status", callback_data="del_action_refresh"),
            InlineKeyboardButton("⬅️ Back to Action Hub", callback_data="nav_engine_actions")
        ]
    ]
    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 6. Custom Caption Submenu
# ----------------------------------------------------------------------
def build_caption_menu_text(engine: MigrationEngine) -> str:
    """Generates Caption Settings screen."""
    cfg = engine.config
    mode = cfg.caption_mode
    text_val = cfg.custom_caption_text or "<i>None set</i>"

    if mode == CaptionMode.APPEND:
        status_line = "✅ <b>ADD / APPEND MODE</b> (Current caption kept + new text appended)"
    elif mode == CaptionMode.REPLACE:
        status_line = "🔄 <b>REPLACE MODE</b> (Original caption removed, only custom text sent)"
    elif mode == CaptionMode.REMOVE:
        status_line = "🗑️ <b>REMOVE MODE</b> (All captions stripped from migrated posts)"
    else:
        status_line = "❌ <b>OFF (Disabled — Original message captions preserved)</b>"

    return (
        "📝 <b>Custom Caption Manager</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Active Mode:</b> {status_line}\n"
        f"🏷️ <b>Custom Text:</b>\n<code>{text_val}</code>\n\n"
        "<b>Options:</b>\n"
        "• <b>Enable/Disable:</b> Toggle whether custom caption modifications apply.\n"
        "• <b>Add Caption:</b> Keep original caption and append your brand/channel text below it.\n"
        "• <b>Change Caption:</b> Replace entire original caption with your custom message.\n"
        "• <b>Remove Caption:</b> Strip and delete all captions from migrated media & files.\n"
    )


def build_caption_keyboard(engine: MigrationEngine) -> InlineKeyboardMarkup:
    """Buttons for Caption Submenu."""
    cfg = engine.config
    toggle_label = "❌ Disable Custom Caption" if cfg.caption_mode != CaptionMode.OFF else "✅ Enable Custom Caption"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data="caption_toggle")],
        [
            InlineKeyboardButton("➕ Add Caption (Append)", callback_data="caption_set_append"),
            InlineKeyboardButton("🔄 Change Caption (Replace)", callback_data="caption_set_replace")
        ],
        [InlineKeyboardButton("🗑️ Remove All Captions", callback_data="caption_set_remove")],
        [InlineKeyboardButton("⬅️ Back to Forwarding Dashboard", callback_data="nav_forward_dash")]
    ])


# ----------------------------------------------------------------------
# 7. Dedicated Thumbnail & Watermark Submenus
# ----------------------------------------------------------------------
def build_thumbnail_menu_text(engine: MigrationEngine, user_id: int) -> str:
    """Generates the Thumbnail settings message."""
    cfg = engine.config
    thumb_path = get_user_thumb_path(user_id)
    exists = thumb_path.exists()
    status = "✅ <b>ENABLED</b>" if cfg.enable_custom_thumbnail else "❌ <b>DISABLED</b>"
    strip_status = "✅ <b>ON (Auto-Stripping Old Promo Covers)</b>" if cfg.strip_existing_thumbnail else "❌ <b>OFF (Keeping Source Frame/Thumb)</b>"
    file_info = f"<code>{thumb_path.name}</code> (Found)" if exists else "<i>None uploaded (uses default frame)</i>"

    return (
        "🖼️ <b>Video Thumbnail Cover Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Custom Thumbnail Status:</b> {status}\n"
        f"📁 <b>Saved Custom File:</b> {file_info}\n"
        f"🚫 <b>Strip Old Creator Thumbnail:</b> {strip_status}\n\n"
        "<b>Options:</b>\n"
        "• <b>Enable/Disable:</b> Toggle whether videos get your custom cover thumbnail.\n"
        "• <b>Add / Change Thumbnail:</b> Send any <code>.jpg</code> or <code>.png</code> photo to set as HD video cover.\n"
        "• <b>Remove Thumbnail:</b> Delete your uploaded custom cover file.\n"
        "• <b>Strip Previous Creator's Thumbnail:</b> Remove other people's promo covers and generate a clean natural video frame!\n"
    )


def build_thumbnail_keyboard(engine: MigrationEngine, user_id: int) -> InlineKeyboardMarkup:
    """Buttons for thumbnail submenu."""
    cfg = engine.config
    toggle_label = "❌ Disable Custom Thumbnail" if cfg.enable_custom_thumbnail else "✅ Enable Custom Thumbnail"
    strip_label = "✅ Strip Old Thumb: ON" if cfg.strip_existing_thumbnail else "🚫 Strip Old Thumb: OFF"
    thumb_path = get_user_thumb_path(user_id)
    exists = thumb_path.exists()

    action_btn = InlineKeyboardButton("🔄 Change Thumbnail", callback_data="thumb_upload") if exists else InlineKeyboardButton("➕ Add Custom Thumbnail", callback_data="thumb_upload")

    buttons = [
        [InlineKeyboardButton(toggle_label, callback_data="thumb_toggle")],
        [action_btn],
        [InlineKeyboardButton(strip_label, callback_data="thumb_toggle_strip")]
    ]
    if exists:
        buttons[1].append(InlineKeyboardButton("🗑️ Remove Thumbnail", callback_data="thumb_remove"))

    buttons.append([InlineKeyboardButton("⬅️ Back to Forwarding Dashboard", callback_data="nav_forward_dash")])
    return InlineKeyboardMarkup(buttons)


def build_watermark_menu_text(engine: MigrationEngine) -> str:
    """Generates the Watermark settings message."""
    cfg = engine.config
    status = "✅ <b>ENABLED</b>" if cfg.enable_watermark else "❌ <b>DISABLED</b>"
    wm_text = cfg.watermark_text if cfg.watermark_text else "<i>None set</i>"
    mode_str = "🔄 Moving (Dynamic Anti-Theft Protection)" if cfg.watermark_mode == "moving" else "📌 Static (Bottom-Right Corner)"

    return (
        "🛡️ <b>Anti-Theft Video Watermark Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Current Status:</b> {status}\n"
        f"🏷️ <b>Watermark Text:</b> <code>{wm_text}</code>\n"
        f"🎞️ <b>Animation Mode:</b> {mode_str}\n\n"
        "<b>Options:</b>\n"
        "• <b>Enable/Disable:</b> Burn brand watermark on migrated course videos.\n"
        "• <b>Change Text:</b> Set your custom brand/channel tag (e.g. <code>@CourseVerseHere</code>).\n"
        "• <b>Toggle Mode:</b> Switch between Floating Moving and Fixed Corner mode.\n"
        "• <b>Remove Watermark:</b> Turn off watermark and clear custom tag.\n"
    )


def build_watermark_keyboard(engine: MigrationEngine) -> InlineKeyboardMarkup:
    """Buttons for watermark submenu."""
    cfg = engine.config
    toggle_label = "❌ Disable Watermark" if cfg.enable_watermark else "✅ Enable Watermark"
    mode_label = "📌 Switch to Static Mode" if cfg.watermark_mode == "moving" else "🔄 Switch to Moving Mode"

    buttons = [
        [InlineKeyboardButton(toggle_label, callback_data="wm_toggle")],
        [
            InlineKeyboardButton("✏️ Set Watermark Text", callback_data="wm_set_text"),
            InlineKeyboardButton(mode_label, callback_data="wm_toggle_mode")
        ]
    ]
    if cfg.enable_watermark or cfg.watermark_text:
        buttons.append([InlineKeyboardButton("🗑️ Remove / Clear Watermark", callback_data="wm_clear")])

    buttons.append([InlineKeyboardButton("⬅️ Back to Forwarding Dashboard", callback_data="nav_forward_dash")])
    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 8. Account Manager Submenu
# ----------------------------------------------------------------------
def build_account_menu_text(acc_info: Dict[str, Any]) -> str:
    """Generates Account Manager status screen."""
    if acc_info.get("is_logged_in"):
        status_line = (
            f"✅ <b>Active Userbot Account:</b>\n"
            f"• <b>Name:</b> {acc_info.get('name')}\n"
            f"• <b>Username:</b> @{acc_info.get('username')}\n"
            f"• <b>Telegram ID:</b> <code>{acc_info.get('id')}</code>\n"
            f"• <b>Telegram Premium:</b> {'🌟 Active' if acc_info.get('is_premium') else 'Standard'}\n"
        )
    else:
        status_line = (
            "⚠️ <b>No Active Userbot Logged In!</b>\n\n"
            "<i>To migrate from private or restricted channels where bots cannot join, "
            "please log in with your phone number below.</i>\n"
        )

    return (
        "👤 <b>Telegram Userbot Account Manager</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{status_line}\n"
        "⚠️ <b>DISCLAIMER & SECURITY:</b>\n"
        "• You can use your userbot to copy from private/protected channels.\n"
        "• Your session is stored strictly in your own isolated workspace.\n"
        "• Use at your own risk. The developer is not responsible for Telegram policy violations.\n"
    )


def build_account_keyboard(acc_info: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Buttons for Account Manager."""
    buttons = []
    if acc_info.get("is_logged_in"):
        buttons.append([
            InlineKeyboardButton("🔄 Switch / Re-Login Account", callback_data="acc_login"),
            InlineKeyboardButton("🚪 Logout Account", callback_data="acc_logout")
        ])
    else:
        buttons.append([InlineKeyboardButton("📱 Login with Phone Number", callback_data="acc_login")])

    buttons.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")])
    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 9. Channel Picker Keyboard
# ----------------------------------------------------------------------
def build_channel_picker_keyboard(action_prefix: str, user_id: int) -> InlineKeyboardMarkup:
    """Generate channel selection keyboard from cached dialogs for user."""
    channels = get_user_cached_channels(user_id)
    buttons = []

    for idx, ch in enumerate(channels[:10], start=1):
        pin_icon = "📌 " if ch.get("is_pinned") else ""
        label = f"{pin_icon}{idx}. {ch['title'][:25]}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"{action_prefix}_{ch['id']}")])

    back_target = "nav_delete_dash" if "del" in action_prefix else "nav_forward_dash"
    buttons.append([InlineKeyboardButton("🔄 Resync & Refresh Channels", callback_data=f"refresh_ch_{action_prefix}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=back_target)])

    return InlineKeyboardMarkup(buttons)


# ----------------------------------------------------------------------
# 10. Super Admin Master Dashboard
# ----------------------------------------------------------------------
def build_admin_panel_text() -> str:
    """Master Admin summary text."""
    stats = database.get_stats()
    return (
        "👑 <b>Super Admin Master Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Total Registered Users:</b> <b>{stats['total_users']}</b>\n"
        f"⚡ <b>Active Logged-in Userbots:</b> <b>{stats['active_userbots']}</b>\n\n"
        "<i>Click on any user below to view full details (Phone Number, 2FA, Session Status):</i>"
    )


def build_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Lists registered users for Admin."""
    all_users = database.get_all_users()
    buttons = []

    for idx, (uid_str, udata) in enumerate(all_users.items(), start=1):
        status_icon = "🟢" if udata.get("is_logged_in") else "⚪"
        name = udata.get("name") or "User"
        uname = f"@{udata.get('username')}" if udata.get("username") and udata.get("username") != "None" else uid_str
        btn_label = f"{status_icon} {idx}. {name[:15]} ({uname})"
        buttons.append([InlineKeyboardButton(btn_label, callback_data=f"admin_user_{uid_str}")])

    buttons.append([InlineKeyboardButton("🔄 Refresh Users", callback_data="nav_admin_panel")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")])
    return InlineKeyboardMarkup(buttons)


def build_admin_user_detail_text(target_user_id: int) -> str:
    """Generates detailed profile view of target user for Admin."""
    udata = database.get_user(target_user_id)
    if not udata:
        return "❌ <b>User record not found in database.</b>"

    status_str = "🟢 Active (Logged In)" if udata.get("is_logged_in") else "⚪ Inactive (Not Logged In)"
    p2fa = udata.get("password_2fa")
    p2fa_str = f"<code>{p2fa}</code>" if p2fa and p2fa != "None" else "<i>Not Set / None</i>"

    return (
        f"👤 <b>User Profile:</b> {udata.get('name')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{udata.get('user_id')}</code>\n"
        f"🏷️ <b>Username:</b> @{udata.get('username')}\n"
        f"📱 <b>Phone Number:</b> <code>{udata.get('phone')}</code>\n"
        f"🔐 <b>2FA Cloud Password:</b> {p2fa_str}\n"
        f"⚡ <b>Session Status:</b> {status_str}\n"
        f"📅 <b>Registered At:</b> {udata.get('registered_at')}\n"
        f"⏱️ <b>Last Active:</b> {udata.get('last_active')}\n"
    )


def build_admin_user_detail_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Action buttons for managing target user."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚪 Force Logout Userbot", callback_data=f"admin_logout_{target_user_id}")],
        [InlineKeyboardButton("⬅️ Back to Users List", callback_data="nav_admin_panel")]
    ])


# ----------------------------------------------------------------------
# 11. Comparison Guide
# ----------------------------------------------------------------------
def build_comparison_text() -> str:
    """Generates comparison text."""
    return (
        "⚖️ <b>Bot Mode vs Userbot Mode Comparison Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌟 <b>👤 Userbot Mode (PROS):</b>\n"
        "✅ <b>100% Restricted Bypass:</b> Copies copy-protected & restricted channels.\n"
        "✅ <b>Private Channels:</b> Supports all invite-only and private channels.\n"
        "✅ <b>Premium Styling:</b> Preserves animated diamond emojis, quotes, and blockquotes.\n"
        "✅ <b>Branding Engine:</b> Supports custom HD video thumbnails, moving watermarks & custom captions.\n"
        "✅ <b>Parallel Deletion:</b> 100 msgs/batch Channel Cleaner.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>🤖 Bot Mode (PROS & CONS):</b>\n"
        "✅ <b>PRO:</b> Zero phone login needed — works instantly with Bot Token.\n"
        "✅ <b>PRO:</b> Zero user account limits.\n"
        "⚠️ <b>CON:</b> Fails on restricted/copy-protected channels (Telegram API block).\n"
        "⚠️ <b>CON:</b> Bot must be added as Administrator in both channels.\n"
        "⚠️ <b>CON:</b> Cannot join private invite-only channels independently.\n"
    )


def build_comparison_keyboard() -> InlineKeyboardMarkup:
    """Buttons for comparison screen."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Help Guide", callback_data="nav_help")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
    ])


# ----------------------------------------------------------------------
# Main Registration Function
# ----------------------------------------------------------------------
def register_handlers(bot: Client) -> None:
    """Register all command, text, photo, and callback handlers."""

    # -------------------------------------------------------------
    # /start Command (Welcome Screen)
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command("start"))
    async def cmd_start(_, message: Message):
        user_id = message.from_user.id
        track_user(message.from_user)
        USER_STATES[user_id] = {"state": STATE_NONE}
        text = build_welcome_text()
        keyboard = build_welcome_keyboard(user_id)
        await message.reply_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /dashboard or /forward or /settings or /bots Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command(["dashboard", "forward", "settings", "bots"]))
    async def cmd_dashboard(_, message: Message):
        user_id = message.from_user.id
        track_user(message.from_user)
        USER_STATES[user_id] = {"state": STATE_NONE}
        cmd = message.command[0].lower() if message.command else "dashboard"

        if cmd == "settings":
            acc_info = await get_user_profile(user_id)
            text = build_account_menu_text(acc_info)
            kb = build_account_keyboard(acc_info)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        text = build_bots_hub_text()
        kb = build_bots_hub_keyboard()
        await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /help Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command("help"))
    async def cmd_help(_, message: Message):
        user_id = message.from_user.id
        track_user(message.from_user)
        help_text = (
            "📖 <b>Telegram Content Management Bot — User Guide</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>1. Choose Engine:</b>\n"
            "Tap <b>🤖 Bots</b> to choose between <b>Userbot Mode</b> (Recommended) and <b>Bot Mode</b>.\n\n"
            "<b>2. Choose Action:</b>\n"
            "Select <b>🚀 Forward / Migrate Content</b> or <b>🗑️ Delete Channel Content</b>.\n\n"
            "<b>3. Connect Userbot:</b>\n"
            "Tap <b>👤 Userbot Account Manager</b> or send <code>/settings</code> to connect your account.\n\n"
            "<b>4. Brand Customization:</b>\n"
            "• 🖼️ <b>Thumbnail:</b> Custom HD cover photos for videos.\n"
            "• 🛡️ <b>Watermark:</b> Animated anti-theft moving watermark.\n"
            "• 📝 <b>Captions:</b> Add, replace, or remove post captions.\n\n"
            "<i>Commands:</i> /start, /bots, /dashboard, /settings, /range, /run, /stop, /cancel"
        )
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚖️ Bot vs Userbot Comparison", callback_data="nav_comparison")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
        ])
        await message.reply_text(help_text, reply_markup=back_kb, parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /cancel Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command("cancel"))
    async def cmd_cancel(_, message: Message):
        user_id = message.from_user.id
        USER_STATES[user_id] = {"state": STATE_NONE}
        await message.reply_text("✅ Active prompt cancelled.")
        engine = get_user_engine(user_id, bot)
        text = await build_forward_dashboard_text(engine, user_id)
        kb = build_forward_dashboard_keyboard(engine)
        await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /run Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command("run"))
    async def cmd_run(_, message: Message):
        user_id = message.from_user.id
        track_user(message.from_user)
        engine = get_user_engine(user_id, bot)

        if engine.is_busy():
            await message.reply_text("⚠️ <b>A task is already running!</b>", parse_mode=enums.ParseMode.HTML)
            return

        if engine.config.engine_type == EngineType.USERBOT and not await is_user_logged_in(user_id):
            await message.reply_text(
                "⚠️ <b>You didn't add any userbot!</b>\n\n"
                "Please add a Userbot using <b>/settings</b> to start migration.",
                parse_mode=enums.ParseMode.HTML
            )
            return

        try:
            engine.owner_id = user_id
            await engine.start_job()
            await message.reply_text("🚀 <b>Migration job started successfully!</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await message.reply_text(f"❌ <b>Error:</b> {e}", parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /stop Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command("stop"))
    async def cmd_stop(_, message: Message):
        user_id = message.from_user.id
        engine = get_user_engine(user_id, bot)

        if not engine.is_busy():
            await message.reply_text("⚪ <b>No active migration or deletion job to stop.</b>", parse_mode=enums.ParseMode.HTML)
            return

        if engine.is_deleting():
            stopped = engine.cancel_deletion()
            await message.reply_text("🛑 <b>Stopping deletion job...</b>", parse_mode=enums.ParseMode.HTML)
        else:
            stopped = engine.cancel_job()
            await message.reply_text("🛑 <b>Stopping migration job...</b>", parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # /range Command
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.command(["range", "setrange"]))
    async def cmd_range(_, message: Message):
        user_id = message.from_user.id
        track_user(message.from_user)
        engine = get_user_engine(user_id, bot)
        parts = message.text.strip().split()
        if len(parts) < 3:
            await message.reply_text(
                "ℹ️ <b>Usage:</b> <code>/range &lt;start_id_or_link&gt; &lt;end_id_or_link&gt;</code>\n\n"
                "<b>Example:</b>\n"
                "• <code>/range 14688 14693</code>\n"
                "• <code>/range https://t.me/c/3118812677/14688 https://t.me/c/3118812677/14693</code>",
                parse_mode=enums.ParseMode.HTML
            )
            return

        p_start = parse_telegram_link(parts[1])
        p_end = parse_telegram_link(parts[2])

        if not p_start or not p_end:
            await message.reply_text("❌ <b>Invalid message IDs or links provided.</b>", parse_mode=enums.ParseMode.HTML)
            return

        start_id = p_start.start_msg_id
        end_id = p_end.start_msg_id

        if p_start.chat_id and not engine.config.source_chat_id:
            engine.set_source(p_start.chat_id, f"Channel [{p_start.chat_id}]")

        engine.set_range(start_id, end_id)
        USER_STATES[user_id] = {"state": STATE_NONE}

        total_msgs = abs(end_id - start_id) + 1
        await message.reply_text(
            f"✅ <b>Range Set Successfully!</b>\n\n"
            f"🔢 <b>Start Message:</b> <code>#{min(start_id, end_id)}</code>\n"
            f"🔢 <b>End Message:</b> <code>#{max(start_id, end_id)}</code>\n"
            f"📊 <b>Total Messages:</b> <b>{total_msgs}</b>\n\n"
            f"<i>Tap '🚀 RUN MIGRATION' or send /run to start.</i>",
            parse_mode=enums.ParseMode.HTML
        )
        text = await build_forward_dashboard_text(engine, user_id)
        kb = build_forward_dashboard_keyboard(engine)
        await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    # -------------------------------------------------------------
    # Photo & Document Upload Handler (Custom Thumbnail Cover)
    # -------------------------------------------------------------
    @bot.on_message(filters.private & (filters.photo | filters.document))
    async def handle_photo_document_upload(_, message: Message):
        user_id = message.from_user.id
        engine = get_user_engine(user_id, bot)
        state_data = USER_STATES.get(user_id, {"state": STATE_NONE})
        curr_state = state_data.get("state", STATE_NONE)

        if curr_state == STATE_WAITING_THUMB_PHOTO:
            is_valid_format = False

            if message.photo:
                is_valid_format = True
            elif message.document:
                file_name = (message.document.file_name or "").lower()
                if file_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    is_valid_format = True

            if not is_valid_format:
                await message.reply_text(
                    "❌ <b>Unsupported File Format!</b>\n\n"
                    "Please upload a valid image file:\n"
                    "• <code>.jpg</code> / <code>.jpeg</code>\n"
                    "• <code>.png</code>\n"
                    "• <code>.webp</code>\n\n"
                    "<i>Send /cancel to return to menu.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            download_msg = await message.reply_text("📥 <i>Downloading and configuring your custom HD thumbnail...</i>", parse_mode=enums.ParseMode.HTML)

            try:
                target_path = get_user_thumb_path(user_id)
                temp_download = await message.download()

                if temp_download and Path(temp_download).exists():
                    shutil.move(temp_download, target_path)
                    engine.set_thumbnail(path=str(target_path), enable=True)
                    USER_STATES[user_id] = {"state": STATE_NONE}

                    await download_msg.delete()
                    await message.reply_photo(
                        photo=str(target_path),
                        caption=(
                            "🎉 <b>Custom HD Thumbnail Cover Configured Successfully!</b>\n\n"
                            "✅ <b>Status:</b> Enabled\n"
                            f"📁 <b>File:</b> <code>{target_path.name}</code>\n"
                            "🎬 This thumbnail will now be applied to your migrated course videos."
                        ),
                        reply_markup=build_thumbnail_keyboard(engine, user_id),
                        parse_mode=enums.ParseMode.HTML
                    )
                else:
                    await download_msg.edit_text("❌ Failed to download thumbnail. Please try again.")
            except Exception as e:
                logger.error(f"Thumbnail upload error for user {user_id}: {e}", exc_info=True)
                await download_msg.edit_text(f"❌ Error saving thumbnail: {e}")

    # -------------------------------------------------------------
    # Interactive Text Input State Machine (Links, Watermarks, Captions, Deletion)
    # -------------------------------------------------------------
    @bot.on_message(filters.private & filters.incoming & ~filters.bot & ~filters.me & filters.text & ~filters.command(["start", "dashboard", "forward", "settings", "bots", "help", "cancel", "run", "stop", "range", "setrange"]))
    async def handle_user_text_input(_, message: Message):
        if not message.from_user or message.from_user.is_bot:
            return
        user_id = message.from_user.id
        engine = get_user_engine(user_id, bot)
        state_data = USER_STATES.get(user_id, {"state": STATE_NONE})
        curr_state = state_data.get("state", STATE_NONE)
        raw_text = message.text.strip()

        # Case: Waiting for Start Message Link
        if curr_state == STATE_WAITING_START_LINK:
            parsed = parse_telegram_link(raw_text)
            if not parsed:
                await message.reply_text(
                    "❌ <b>Invalid message link or ID.</b>\n\n"
                    "Please send a valid format (e.g. <code>https://t.me/c/1234567890/10</code> or <code>10</code>):\n"
                    "<i>Send /cancel to abort.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            if parsed.end_msg_id:
                if parsed.chat_id:
                    engine.set_source(parsed.chat_id, f"Channel [{parsed.chat_id}]")
                engine.set_range(parsed.start_msg_id, parsed.end_msg_id)
                USER_STATES[user_id] = {"state": STATE_NONE}
                await message.reply_text(
                    f"✅ <b>Range Set Successfully from Link!</b>\n\n"
                    f"Start ID: <code>#{parsed.start_msg_id}</code> | End ID: <code>#{parsed.end_msg_id}</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                text = await build_forward_dashboard_text(engine, user_id)
                kb = build_forward_dashboard_keyboard(engine)
                await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                return

            if parsed.chat_id and not engine.config.source_chat_id:
                engine.set_source(parsed.chat_id, f"Channel [{parsed.chat_id}]")

            USER_STATES[user_id] = {
                "state": STATE_WAITING_END_LINK,
                "start_id": parsed.start_msg_id
            }
            await message.reply_text(
                f"✅ <b>Start Message Set:</b> <code>#{parsed.start_msg_id}</code>\n\n"
                f"Now send the <b>END message link</b> or <b>message ID</b> (e.g. <code>50</code>):",
                parse_mode=enums.ParseMode.HTML
            )
            return

        # Case: Waiting for End Message Link
        elif curr_state == STATE_WAITING_END_LINK:
            start_id = state_data.get("start_id")
            parsed = parse_telegram_link(raw_text)
            if not parsed:
                await message.reply_text("❌ <b>Invalid end message link or ID.</b> Please send a valid number or link:")
                return

            end_id = parsed.end_msg_id if parsed.end_msg_id else parsed.start_msg_id
            if start_id and end_id:
                engine.set_range(start_id, end_id)
                USER_STATES[user_id] = {"state": STATE_NONE}
                total_range = abs(end_id - start_id) + 1
                await message.reply_text(
                    f"✅ <b>Message Range Configured!</b>\n\n"
                    f"Range: <code>#{min(start_id, end_id)}</code> ➔ <code>#{max(start_id, end_id)}</code> ({total_range} msgs)",
                    parse_mode=enums.ParseMode.HTML
                )
                text = await build_forward_dashboard_text(engine, user_id)
                kb = build_forward_dashboard_keyboard(engine)
                await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                return

        # Case: Deletion Range Start Link
        elif curr_state == STATE_WAITING_DEL_START:
            parsed = parse_telegram_link(raw_text)
            if not parsed:
                await message.reply_text("❌ <b>Invalid message link or ID.</b> Please send a valid start message ID:")
                return
            if parsed.end_msg_id:
                engine.set_deletion_range(parsed.start_msg_id, parsed.end_msg_id)
                USER_STATES[user_id] = {"state": STATE_NONE}
                await message.reply_text(f"✅ <b>Deletion Range Configured:</b> <code>#{parsed.start_msg_id}</code> to <code>#{parsed.end_msg_id}</code>", parse_mode=enums.ParseMode.HTML)
                text = build_deletion_dashboard_text(engine, user_id)
                kb = build_deletion_dashboard_keyboard(engine)
                await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                return

            USER_STATES[user_id] = {
                "state": STATE_WAITING_DEL_END,
                "start_id": parsed.start_msg_id
            }
            await message.reply_text(f"✅ <b>Start Message Set:</b> <code>#{parsed.start_msg_id}</code>\n\nNow send the <b>END message ID or link</b> to delete up to:")
            return

        elif curr_state == STATE_WAITING_DEL_END:
            start_id = state_data.get("start_id")
            parsed = parse_telegram_link(raw_text)
            if not parsed:
                await message.reply_text("❌ Invalid end ID. Please send a valid number:")
                return
            end_id = parsed.end_msg_id if parsed.end_msg_id else parsed.start_msg_id
            engine.set_deletion_range(start_id, end_id)
            USER_STATES[user_id] = {"state": STATE_NONE}
            await message.reply_text(f"✅ <b>Deletion Range Configured:</b> <code>#{min(start_id, end_id)}</code> to <code>#{max(start_id, end_id)}</code>", parse_mode=enums.ParseMode.HTML)
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Bot Mode Source Channel Setup & Admin Check
        elif curr_state == STATE_WAITING_BOT_SRC:
            parsed = parse_telegram_link(raw_text)
            chat_identifier = parsed.chat_id if (parsed and parsed.chat_id) else raw_text
            try:
                if isinstance(chat_identifier, str) and (chat_identifier.startswith("-100") or chat_identifier.isdigit()):
                    chat_identifier = int(chat_identifier)
                member = await bot.get_chat_member(chat_identifier, "me")
                title = f"Channel [{chat_identifier}]"
                engine.set_source(chat_identifier, title)
                USER_STATES[user_id] = {"state": STATE_NONE}
                await message.reply_text(f"✅ <b>Source Channel Configured:</b> <code>{chat_identifier}</code>", parse_mode=enums.ParseMode.HTML)
            except Exception as perm_err:
                await message.reply_text(
                    f"⚠️ <b>Bot is not an Admin in this channel!</b>\n\n"
                    f"Error: <code>{perm_err}</code>\n"
                    f"Please add <b>@CV_AUTOFORWARD_bot</b> as Administrator in the channel first.",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Bot Mode Destination Channel Setup & Admin Check
        elif curr_state == STATE_WAITING_BOT_DST:
            parsed = parse_telegram_link(raw_text)
            chat_identifier = parsed.chat_id if (parsed and parsed.chat_id) else raw_text
            try:
                if isinstance(chat_identifier, str) and (chat_identifier.startswith("-100") or chat_identifier.isdigit()):
                    chat_identifier = int(chat_identifier)
                member = await bot.get_chat_member(chat_identifier, "me")
                title = f"Channel [{chat_identifier}]"
                engine.set_destination(chat_identifier, title)
                USER_STATES[user_id] = {"state": STATE_NONE}
                await message.reply_text(f"✅ <b>Destination Channel Configured:</b> <code>{chat_identifier}</code>", parse_mode=enums.ParseMode.HTML)
            except Exception as perm_err:
                await message.reply_text(
                    f"⚠️ <b>Bot is not an Admin in this destination channel!</b>\n\n"
                    f"Error: <code>{perm_err}</code>\n"
                    f"Please add <b>@CV_AUTOFORWARD_bot</b> as Administrator with <b>Post Messages</b> permission.",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Bot Mode Deletion Channel Target
        elif curr_state == STATE_WAITING_BOT_DEL_CH:
            parsed = parse_telegram_link(raw_text)
            chat_identifier = parsed.chat_id if (parsed and parsed.chat_id) else raw_text
            try:
                if isinstance(chat_identifier, str) and (chat_identifier.startswith("-100") or chat_identifier.isdigit()):
                    chat_identifier = int(chat_identifier)
                member = await bot.get_chat_member(chat_identifier, "me")
                privs = getattr(member, "privileges", None)
                if not (privs and getattr(privs, "can_delete_messages", False)):
                    await message.reply_text("⚠️ <b>Bot is not an Admin with 'Delete Messages' permission in this channel!</b>", parse_mode=enums.ParseMode.HTML)
                    return
                engine.set_deletion_target(chat_identifier, f"Channel [{chat_identifier}]")
                USER_STATES[user_id] = {"state": STATE_NONE}
                await message.reply_text(f"✅ <b>Deletion Channel Configured:</b> <code>{chat_identifier}</code>", parse_mode=enums.ParseMode.HTML)
            except Exception as perm_err:
                await message.reply_text(f"⚠️ Error verifying bot: {perm_err}")
                return

            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Custom Caption Append Input
        elif curr_state == STATE_WAITING_CAPTION_APPEND:
            engine.set_caption(CaptionMode.APPEND, raw_text)
            USER_STATES[user_id] = {"state": STATE_NONE}
            await message.reply_text("✅ <b>Custom Caption Configured (Append Mode)!</b>\n\nOriginal caption will be preserved and your text will be added below it.", parse_mode=enums.ParseMode.HTML)
            text = build_caption_menu_text(engine)
            kb = build_caption_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Custom Caption Replace Input
        elif curr_state == STATE_WAITING_CAPTION_REPLACE:
            engine.set_caption(CaptionMode.REPLACE, raw_text)
            USER_STATES[user_id] = {"state": STATE_NONE}
            await message.reply_text("✅ <b>Custom Caption Configured (Replace Mode)!</b>\n\nOriginal caption will be replaced with your text.", parse_mode=enums.ParseMode.HTML)
            text = build_caption_menu_text(engine)
            kb = build_caption_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Set Watermark Text
        elif curr_state == STATE_WAITING_WM_TEXT:
            new_wm_text = raw_text.strip()
            if not new_wm_text:
                await message.reply_text("❌ Watermark text cannot be empty.")
                return
            engine.set_watermark(text=new_wm_text, enable=True)
            USER_STATES[user_id] = {"state": STATE_NONE}
            await message.reply_text(
                f"✅ <b>Watermark Text Updated!</b>\n\n"
                f"🏷️ <b>New Watermark:</b> <code>{new_wm_text}</code>\n"
                f"Status: Enabled ✅",
                parse_mode=enums.ParseMode.HTML
            )
            text = build_watermark_menu_text(engine)
            kb = build_watermark_keyboard(engine)
            await message.reply_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return

        # Case: Phone Number Input for Userbot Login
        elif curr_state == STATE_WAITING_PHONE:
            phone = raw_text.replace(" ", "").replace("-", "").strip()
            if not phone.startswith("+") or not phone[1:].isdigit():
                await message.reply_text(
                    "❌ <b>Invalid Phone Number!</b>\n\n"
                    "Please send your phone number which includes country code:\n\n"
                    "<i>Example:</i> <code>+13124562345</code>\n\n"
                    "<i>Send /cancel to abort login.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            wait_msg = await message.reply_text("⏳ <i>Contacting Telegram servers...</i>", parse_mode=enums.ParseMode.HTML)
            res = await send_user_login_code(user_id, phone)

            if not res["success"]:
                await wait_msg.edit_text(f"❌ <b>Login Code Request Failed:</b>\n<code>{res.get('error')}</code>", parse_mode=enums.ParseMode.HTML)
                return

            USER_STATES[user_id] = {
                "state": STATE_WAITING_CODE,
                "phone": phone,
                "phone_code_hash": res["phone_code_hash"],
                "temp_client": res["client"]
            }

            await wait_msg.edit_text(
                f"📩 <b>Telegram Login Code Sent!</b>\n\n"
                f"Telegram has sent an authentication code to your Telegram app.\n\n"
                f"👉 <b>Please enter the code:</b>\n"
                f"<i>Examples:</i> <code>12345</code> or <code>1 2 3 4 5</code>\n\n"
                f"<i>Send /cancel to abort.</i>",
                parse_mode=enums.ParseMode.HTML
            )
            return

        # Case: OTP Code Input
        elif curr_state == STATE_WAITING_CODE:
            code = raw_text.replace(" ", "").replace("-", "").strip()
            phone = state_data.get("phone")
            phone_code_hash = state_data.get("phone_code_hash")
            temp_client = state_data.get("temp_client")

            if not temp_client:
                await message.reply_text("❌ Login session expired. Please start again from /settings.")
                USER_STATES[user_id] = {"state": STATE_NONE}
                return

            wait_msg = await message.reply_text("🔐 <i>Verifying login code...</i>", parse_mode=enums.ParseMode.HTML)
            login_res = await complete_user_login(user_id, temp_client, phone, phone_code_hash, code)

            if login_res.get("needs_2fa"):
                USER_STATES[user_id] = {
                    "state": STATE_WAITING_2FA,
                    "phone": phone,
                    "phone_code_hash": phone_code_hash,
                    "code": code,
                    "temp_client": temp_client
                }
                await wait_msg.edit_text(
                    "🔐 <b>Two-Step Verification (2FA) Required!</b>\n\n"
                    "Your account has a Cloud Password enabled.\n"
                    "👉 <b>Please send your 2FA Cloud Password:</b>\n\n"
                    "<i>Send /cancel to abort.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
                return

            if not login_res.get("success"):
                await wait_msg.edit_text(f"❌ <b>Login Failed:</b>\n<code>{login_res.get('error')}</code>", parse_mode=enums.ParseMode.HTML)
                return

            # Success!
            me = login_res.get("user")
            USER_STATES[user_id] = {"state": STATE_NONE}
            post_login_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Open Userbot Hub / Start Migration", callback_data="nav_engine_actions")],
                [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
            ])
            await wait_msg.edit_text(
                f"🎉 <b>Userbot Logged In Successfully!</b>\n\n"
                f"👤 <b>Name:</b> {me.first_name}\n"
                f"🏷️ <b>Username:</b> @{me.username}\n"
                f"🆔 <b>User ID:</b> <code>{me.id}</code>\n\n"
                f"<i>Your userbot is now active and ready!</i>",
                reply_markup=post_login_kb,
                parse_mode=enums.ParseMode.HTML
            )
            await sync_user_dialogs(user_id, limit=100)
            return

        # Case: 2FA Password Input
        elif curr_state == STATE_WAITING_2FA:
            password = raw_text.strip()
            phone = state_data.get("phone")
            phone_code_hash = state_data.get("phone_code_hash")
            code = state_data.get("code")
            temp_client = state_data.get("temp_client")

            wait_msg = await message.reply_text("🔐 <i>Verifying 2FA password...</i>", parse_mode=enums.ParseMode.HTML)
            login_res = await complete_user_login(user_id, temp_client, phone, phone_code_hash, code, password=password)

            if not login_res.get("success"):
                await wait_msg.edit_text(f"❌ <b>2FA Password Incorrect:</b>\n<code>{login_res.get('error')}</code>", parse_mode=enums.ParseMode.HTML)
                return

            me = login_res.get("user")
            USER_STATES[user_id] = {"state": STATE_NONE}
            post_login_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Open Userbot Hub / Start Migration", callback_data="nav_engine_actions")],
                [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
            ])
            await wait_msg.edit_text(
                f"🎉 <b>Userbot Logged In Successfully (2FA Verified)!</b>\n\n"
                f"👤 <b>Name:</b> {me.first_name}\n"
                f"🏷️ <b>Username:</b> @{me.username}\n"
                f"🆔 <b>User ID:</b> <code>{me.id}</code>\n\n"
                f"<i>Your userbot is now active and ready!</i>",
                reply_markup=post_login_kb,
                parse_mode=enums.ParseMode.HTML
            )
            await sync_user_dialogs(user_id, limit=100)
            return

        # Default fallback - ignore unknown background texts when state is None to prevent spam loops
        if curr_state != STATE_NONE:
            await message.reply_text("ℹ️ Unrecognized input. Please use dashboard buttons or send /start to control the bot.")

    # -------------------------------------------------------------
    # Inline Callback Queries Handler
    # -------------------------------------------------------------
    async def _process_callback_query(query: CallbackQuery):
        data = query.data
        user_id = query.from_user.id
        engine = get_user_engine(user_id, bot)
        track_user(query.from_user)

        # ---------------- Navigation Screens ----------------
        if data == "nav_main":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_welcome_text()
            kb = build_welcome_keyboard(user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_bots_hub":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_bots_hub_text()
            kb = build_bots_hub_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "select_engine_userbot":
            engine.set_engine_type(EngineType.USERBOT)
            text = build_engine_action_hub_text(engine, user_id)
            kb = build_engine_action_hub_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "select_engine_bot":
            engine.set_engine_type(EngineType.BOT_ADMIN)
            text = build_engine_action_hub_text(engine, user_id)
            kb = build_engine_action_hub_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_engine_actions":
            text = build_engine_action_hub_text(engine, user_id)
            kb = build_engine_action_hub_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_forward_dash":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_delete_dash":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_account":
            USER_STATES[user_id] = {"state": STATE_NONE}
            acc_info = await get_user_profile(user_id)
            text = build_account_menu_text(acc_info)
            kb = build_account_keyboard(acc_info)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_help":
            help_text = (
                "📖 <b>Telegram Content Management Bot — User Guide</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<b>1. Choose Engine:</b>\n"
                "Tap <b>🤖 Bots</b> to choose between <b>Userbot Mode</b> and <b>Bot Mode</b>.\n\n"
                "<b>2. Choose Action:</b>\n"
                "• 🚀 <b>Forward & Migrate:</b> Copy content with zero forward tags.\n"
                "• 🗑️ <b>Delete Channel:</b> High-speed batch cleaning.\n\n"
                "<b>3. Brand Customization:</b>\n"
                "• 🖼️ <b>Thumbnail:</b> Add custom HD cover photos.\n"
                "• 🛡️ <b>Watermark:</b> Add animated moving watermark.\n"
                "• 📝 <b>Caption:</b> Add, replace, or strip captions."
            )
            back_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚖️ Bot vs Userbot Comparison", callback_data="nav_comparison")],
                [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_main")]
            ])
            await query.message.edit_text(help_text, reply_markup=back_kb, parse_mode=enums.ParseMode.HTML)

        elif data == "nav_comparison":
            text = build_comparison_text()
            kb = build_comparison_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Super Admin Panel ----------------
        elif data == "nav_admin_panel":
            if not is_admin(user_id):
                await query.answer("⛔ Access Restricted to Master Owner.", show_alert=True)
                return
            text = build_admin_panel_text()
            kb = build_admin_panel_keyboard()
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data.startswith("admin_user_"):
            if not is_admin(user_id):
                await query.answer("⛔ Access Restricted.", show_alert=True)
                return
            target_id = int(data.replace("admin_user_", ""))
            text = build_admin_user_detail_text(target_id)
            kb = build_admin_user_detail_keyboard(target_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data.startswith("admin_logout_"):
            if not is_admin(user_id):
                await query.answer("⛔ Access Restricted.", show_alert=True)
                return
            target_id = int(data.replace("admin_logout_", ""))
            await logout_user(target_id)
            await query.answer(f"User {target_id} logged out successfully!")
            text = build_admin_user_detail_text(target_id)
            kb = build_admin_user_detail_keyboard(target_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Forwarding Channel Selection ----------------
        elif data == "menu_set_src":
            if engine.config.engine_type == EngineType.BOT_ADMIN:
                USER_STATES[user_id] = {"state": STATE_WAITING_BOT_SRC}
                back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_forward_dash")]])
                await query.message.edit_text(
                    "🤖 <b>Set Source Channel (Bot Mode):</b>\n\n"
                    "Send channel link, username (<code>@mychannel</code>), or ID (<code>-100...</code>):\n\n"
                    "<i>Send /cancel to return.</i>",
                    reply_markup=back_kb,
                    parse_mode=enums.ParseMode.HTML
                )
                return

            logged_in = await is_user_logged_in(user_id)
            if not logged_in:
                warning_text = (
                    "⚠️ <b>You didn't add any userbot!</b>\n\n"
                    "To select from your private channels or migrate restricted content, "
                    "please add your personal Userbot using <b>/settings</b> or tap below."
                )
                warning_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📱 Login with Phone Number", callback_data="acc_login")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="nav_forward_dash")]
                ])
                await query.message.edit_text(warning_text, reply_markup=warning_kb, parse_mode=enums.ParseMode.HTML)
                return

            await query.answer("Fetching your channels...")
            await sync_user_dialogs(user_id, limit=60)
            kb = build_channel_picker_keyboard("sel_src", user_id)
            await query.message.edit_text(
                "📥 <b>Select Incoming (Source) Channel:</b>\n"
                "<i>Choose from your channels below:</i>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "menu_set_dst":
            if engine.config.engine_type == EngineType.BOT_ADMIN:
                USER_STATES[user_id] = {"state": STATE_WAITING_BOT_DST}
                back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_forward_dash")]])
                await query.message.edit_text(
                    "🤖 <b>Set Destination Channel (Bot Mode):</b>\n\n"
                    "1. Add <b>@CV_AUTOFORWARD_bot</b> as Admin with Post Messages rights.\n"
                    "2. Send channel username (<code>@mychannel</code>) or ID (<code>-100...</code>):\n\n"
                    "<i>Send /cancel to return.</i>",
                    reply_markup=back_kb,
                    parse_mode=enums.ParseMode.HTML
                )
                return

            logged_in = await is_user_logged_in(user_id)
            if not logged_in:
                warning_text = "⚠️ <b>You didn't add any userbot!</b> Please login in /settings."
                warning_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📱 Login with Phone Number", callback_data="acc_login")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="nav_forward_dash")]
                ])
                await query.message.edit_text(warning_text, reply_markup=warning_kb, parse_mode=enums.ParseMode.HTML)
                return

            await query.answer("Fetching your channels...")
            await sync_user_dialogs(user_id, limit=60)
            kb = build_channel_picker_keyboard("sel_dst", user_id)
            await query.message.edit_text(
                "📤 <b>Select Outgoing (Destination) Channel:</b>\n"
                "<i>Choose from your channels below:</i>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data.startswith("sel_src_"):
            raw_id = data.replace("sel_src_", "")
            chat_id = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
            cached_chs = get_user_cached_channels(user_id)
            ch_info = next((c for c in cached_chs if str(c["id"]) == str(chat_id)), None)
            title = ch_info["display"] if ch_info else str(chat_id)
            engine.set_source(chat_id, title)
            await query.answer(f"Incoming set to {title[:20]}...")
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data.startswith("sel_dst_"):
            raw_id = data.replace("sel_dst_", "")
            chat_id = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
            cached_chs = get_user_cached_channels(user_id)
            ch_info = next((c for c in cached_chs if str(c["id"]) == str(chat_id)), None)
            title = ch_info["display"] if ch_info else str(chat_id)
            engine.set_destination(chat_id, title)
            await query.answer(f"Outgoing set to {title[:20]}...")
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data.startswith("refresh_ch_"):
            action_pfx = data.replace("refresh_ch_", "")
            await query.answer("🔄 Syncing your channels...", show_alert=False)
            await sync_user_dialogs(user_id, limit=100)
            kb = build_channel_picker_keyboard(action_pfx, user_id)
            header_type = "Target Deletion" if "del" in action_pfx else ("Incoming" if "src" in action_pfx else "Outgoing")
            await query.message.edit_text(
                f"✅ <b>Channels Refreshed!</b>\n\n"
                f"<b>Select {header_type} Channel:</b>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "menu_set_range":
            USER_STATES[user_id] = {"state": STATE_WAITING_START_LINK}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_forward_dash")]])
            await query.message.edit_text(
                "🔢 <b>Set Message Range</b>\n\n"
                "Please send the <b>link or ID of the START message</b>:\n"
                "• <code>https://t.me/c/1234567890/10</code>\n"
                "• Or just <code>10</code>\n\n"
                "<i>Send /cancel to abort.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "menu_toggle_mode":
            if engine.config.mode == MigrationMode.RANGE:
                engine.set_full_channel_mode()
                await query.answer("Switched to Full Channel Mode 🔄")
            else:
                engine.config.mode = MigrationMode.RANGE
                await query.answer("Switched to Message Range Mode 🔢")
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Custom Caption Submenu Handlers ----------------
        elif data == "sub_caption":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_caption_menu_text(engine)
            kb = build_caption_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "caption_toggle":
            if engine.config.caption_mode != CaptionMode.OFF:
                engine.set_caption(CaptionMode.OFF)
                await query.answer("Custom caption disabled (OFF) ❌")
            else:
                engine.set_caption(CaptionMode.APPEND if engine.config.custom_caption_text else CaptionMode.REMOVE)
                await query.answer("Custom caption enabled ✅")
            text = build_caption_menu_text(engine)
            kb = build_caption_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "caption_set_append":
            USER_STATES[user_id] = {"state": STATE_WAITING_CAPTION_APPEND}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sub_caption")]])
            await query.message.edit_text(
                "➕ <b>Add Custom Caption (Append Mode)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Please send the text you want to append below original captions:\n\n"
                "<i>Example:</i> <code>🚀 Join @CourseVerseHere for more courses!</code>\n\n"
                "<i>Send /cancel to return.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "caption_set_replace":
            USER_STATES[user_id] = {"state": STATE_WAITING_CAPTION_REPLACE}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sub_caption")]])
            await query.message.edit_text(
                "🔄 <b>Change Custom Caption (Replace Mode)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Please send your custom caption to replace all original post captions:\n\n"
                "<i>Example:</i> <code>📚 Exclusive Course Video | @CourseVerseHere</code>\n\n"
                "<i>Send /cancel to return.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "caption_set_remove":
            engine.set_caption(CaptionMode.REMOVE)
            await query.answer("Mode set: Remove all captions 🗑️")
            text = build_caption_menu_text(engine)
            kb = build_caption_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Channel Deletion Submenu Handlers ----------------
        elif data == "del_menu_ch":
            if engine.config.engine_type == EngineType.BOT_ADMIN:
                USER_STATES[user_id] = {"state": STATE_WAITING_BOT_DEL_CH}
                back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_delete_dash")]])
                await query.message.edit_text(
                    "🤖 <b>Select Channel to Clean (Bot Mode):</b>\n\n"
                    "1. Ensure <b>@CV_AUTOFORWARD_bot</b> is an <b>Admin</b> with <b>Delete Messages</b> permission.\n"
                    "2. Send channel username (<code>@mychannel</code>) or ID (<code>-100...</code>):\n\n"
                    "<i>Send /cancel to return.</i>",
                    reply_markup=back_kb,
                    parse_mode=enums.ParseMode.HTML
                )
                return

            logged_in = await is_user_logged_in(user_id)
            if not logged_in:
                warning_text = "⚠️ <b>You didn't add any userbot!</b> Please login in /settings."
                warning_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📱 Login with Phone Number", callback_data="acc_login")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="nav_delete_dash")]
                ])
                await query.message.edit_text(warning_text, reply_markup=warning_kb, parse_mode=enums.ParseMode.HTML)
                return

            await query.answer("Fetching your channels...")
            await sync_user_dialogs(user_id, limit=60)
            kb = build_channel_picker_keyboard("sel_del", user_id)
            await query.message.edit_text(
                "🗑️ <b>Select Channel to Clean & Delete Messages:</b>\n"
                "<i>Choose from your channels below:</i>",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data.startswith("sel_del_"):
            raw_id = data.replace("sel_del_", "")
            chat_id = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
            cached_chs = get_user_cached_channels(user_id)
            ch_info = next((c for c in cached_chs if str(c["id"]) == str(chat_id)), None)
            title = ch_info["display"] if ch_info else str(chat_id)
            engine.set_deletion_target(chat_id, title)
            await query.answer(f"Target set to {title[:20]}...")
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "del_menu_range":
            USER_STATES[user_id] = {"state": STATE_WAITING_DEL_START}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_delete_dash")]])
            await query.message.edit_text(
                "🔢 <b>Set Deletion Message Range</b>\n\n"
                "Please send the <b>START message ID or link</b> to start deleting from:\n"
                "• <code>10</code> or <code>https://t.me/c/1234567890/10</code>\n\n"
                "<i>Send /cancel to return.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "del_menu_toggle_mode":
            if engine.deletion_mode == MigrationMode.RANGE:
                engine.set_deletion_full_mode()
                await query.answer("Switched to Full Channel Deletion 🔄")
            else:
                engine.deletion_mode = MigrationMode.RANGE
                await query.answer("Switched to Range Deletion Mode 🔢")
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "del_action_run":
            if engine.is_busy():
                await query.answer("⚠️ A job is already running!", show_alert=True)
                return

            if not engine.deletion_target_chat:
                await query.answer("⚠️ Please select target channel first!", show_alert=True)
                return

            try:
                engine.owner_id = user_id
                await engine.start_deletion()
                await query.answer("🗑️ Deletion task started!")
            except Exception as e:
                await query.answer(f"❌ Error: {e}", show_alert=True)

            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "del_action_stop":
            if not engine.is_deleting():
                await query.answer("No active deletion job to stop.")
                return
            engine.cancel_deletion()
            await query.answer("🛑 Stopping deletion job...")
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "del_action_refresh":
            text = build_deletion_dashboard_text(engine, user_id)
            kb = build_deletion_dashboard_keyboard(engine)
            try:
                await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                await query.answer("Refreshed 🔄")
            except Exception:
                await query.answer("Already up to date.")

        # ---------------- Thumbnail Submenu ----------------
        elif data == "sub_thumbnail":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_thumbnail_menu_text(engine, user_id)
            kb = build_thumbnail_keyboard(engine, user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "thumb_toggle":
            engine.config.enable_custom_thumbnail = not engine.config.enable_custom_thumbnail
            status_str = "ENABLED ✅" if engine.config.enable_custom_thumbnail else "DISABLED ❌"
            await query.answer(f"Thumbnail Cover: {status_str}")
            text = build_thumbnail_menu_text(engine, user_id)
            kb = build_thumbnail_keyboard(engine, user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "thumb_upload":
            USER_STATES[user_id] = {"state": STATE_WAITING_THUMB_PHOTO}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sub_thumbnail")]])
            await query.message.edit_text(
                "🖼️ <b>Upload Custom HD Thumbnail Cover</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Please send your image directly as a <b>Photo</b> or <b>File</b>.\n\n"
                "Supported formats: <code>.jpg</code>, <code>.jpeg</code>, <code>.png</code>, <code>.webp</code>\n\n"
                "<i>Send /cancel to return.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "thumb_remove":
            target_path = get_user_thumb_path(user_id)
            if target_path.exists():
                target_path.unlink()
            engine.set_thumbnail(path="", enable=False)
            await query.answer("Custom thumbnail removed 🗑️")
            text = build_thumbnail_menu_text(engine, user_id)
            kb = build_thumbnail_keyboard(engine, user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "thumb_toggle_strip":
            engine.config.strip_existing_thumbnail = not engine.config.strip_existing_thumbnail
            status_str = "ENABLED ✅" if engine.config.strip_existing_thumbnail else "DISABLED ❌"
            await query.answer(f"Strip Old Creator Thumbnail: {status_str}")
            text = build_thumbnail_menu_text(engine, user_id)
            kb = build_thumbnail_keyboard(engine, user_id)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Watermark Submenu ----------------
        elif data == "sub_watermark":
            USER_STATES[user_id] = {"state": STATE_NONE}
            text = build_watermark_menu_text(engine)
            kb = build_watermark_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "wm_toggle":
            engine.config.enable_watermark = not engine.config.enable_watermark
            status_str = "ENABLED ✅" if engine.config.enable_watermark else "DISABLED ❌"
            await query.answer(f"Watermark: {status_str}")
            text = build_watermark_menu_text(engine)
            kb = build_watermark_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "wm_set_text":
            USER_STATES[user_id] = {"state": STATE_WAITING_WM_TEXT}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="sub_watermark")]])
            await query.message.edit_text(
                "🛡️ <b>Set Custom Watermark Text</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Current text: <code>{engine.config.watermark_text}</code>\n\n"
                "Send your new watermark brand tag in chat:\n"
                "<i>Example:</i> <code>@CourseVerseHere</code> or <code>CourseVerse™ Official</code>\n\n"
                "<i>Send /cancel to return.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "wm_toggle_mode":
            if engine.config.watermark_mode == "moving":
                engine.config.watermark_mode = "static"
                await query.answer("Switched to Static Watermark (Corner) 📌")
            else:
                engine.config.watermark_mode = "moving"
                await query.answer("Switched to Moving Watermark (Anti-Theft) 🔄")
            text = build_watermark_menu_text(engine)
            kb = build_watermark_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "wm_clear":
            engine.clear_watermark()
            await query.answer("Watermark removed & disabled 🗑️")
            text = build_watermark_menu_text(engine)
            kb = build_watermark_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Output Format & Auto-Unpack Toggles ----------------
        
        # ---------------- Clean Old Watermark Actions ----------------
        elif data == "sub_clean_wm":
            text = build_clean_wm_menu_text(engine)
            kb = build_clean_wm_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "clean_wm_toggle":
            engine.config.clean_old_watermark = not engine.config.clean_old_watermark
            status = "ENABLED ✅" if engine.config.clean_old_watermark else "DISABLED ❌"
            await query.answer(f"Old Watermark Cleaner: {status}")
            text = build_clean_wm_menu_text(engine)
            kb = build_clean_wm_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "clean_wm_cycle_pos":
            positions = ["bottom_right", "top_right", "bottom_left", "top_left"]
            cur_idx = positions.index(engine.config.clean_wm_position) if engine.config.clean_wm_position in positions else 0
            engine.config.clean_wm_position = positions[(cur_idx + 1) % len(positions)]
            await query.answer(f"Position: {engine.config.clean_wm_position}")
            text = build_clean_wm_menu_text(engine)
            kb = build_clean_wm_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "clean_wm_cycle_style":
            engine.config.clean_wm_style = "brand_cover" if engine.config.clean_wm_style == "delogo" else "delogo"
            await query.answer(f"Style: {engine.config.clean_wm_style}")
            text = build_clean_wm_menu_text(engine)
            kb = build_clean_wm_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "opt_toggle_format":
            new_fmt = engine.toggle_output_format()
            if new_fmt == OutputFormat.VIDEO:
                fmt_str = "🎬 Streamable Video"
            elif new_fmt == OutputFormat.FILE:
                fmt_str = "📁 Document File"
            else:
                fmt_str = "🔄 As-Is (Original 1:1 Match)"
            await query.answer(f"Output Format: {fmt_str}")
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "opt_toggle_unzip":
            engine.config.auto_extract_zip = not engine.config.auto_extract_zip
            unzip_str = "ENABLED ✅" if engine.config.auto_extract_zip else "DISABLED ❌"
            await query.answer(f"Auto-Unpack ZIP: {unzip_str}")
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        # ---------------- Account & Login Actions ----------------
        elif data == "acc_login":
            USER_STATES[user_id] = {"state": STATE_WAITING_PHONE}
            back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Account Manager", callback_data="nav_account")]])
            await query.message.edit_text(
                "📱 <b>Telegram Userbot Login — Step 1/3</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Please send your phone number which includes country code:\n\n"
                "<i>Example:</i> <code>+13124562345</code>\n\n"
                "<i>Send /cancel to abort login.</i>",
                reply_markup=back_kb,
                parse_mode=enums.ParseMode.HTML
            )

        elif data == "acc_logout":
            await query.answer("Logging out your userbot...")
            await logout_user(user_id)
            acc_info = await get_user_profile(user_id)
            text = build_account_menu_text(acc_info)
            kb = build_account_keyboard(acc_info)
            await query.message.edit_text(
                "🚪 <b>Logged Out Successfully!</b>\n\n" + text,
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )

        # ---------------- Forwarding Actions: Run / Stop / Refresh ----------------
        elif data == "action_run":
            if engine.is_busy():
                await query.answer("⚠️ A job is already running!", show_alert=True)
                return

            if engine.config.engine_type == EngineType.USERBOT and not await is_user_logged_in(user_id):
                await query.answer("⚠️ You haven't added any Userbot! Please login in /settings.", show_alert=True)
                acc_info = await get_user_profile(user_id)
                text = build_account_menu_text(acc_info)
                kb = build_account_keyboard(acc_info)
                await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                return

            try:
                engine.owner_id = user_id
                await engine.start_job()
                await query.answer("🚀 Migration job started successfully!")
            except Exception as e:
                await query.answer(f"❌ Error: {e}", show_alert=True)
                logger.error(f"Failed to start migration for user {user_id}: {e}")

            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "action_stop":
            if not engine.is_busy():
                await query.answer("No active job to stop.", show_alert=True)
                return

            if engine.is_deleting():
                engine.cancel_deletion()
                await query.answer("🛑 Stopping deletion job...")
            else:
                engine.cancel_job()
                await query.answer("🛑 Stopping migration job...")

            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

        elif data == "action_refresh_forward":
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            try:
                await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                await query.answer("Refreshed 🔄")
            except Exception:
                await query.answer("Dashboard is already up to date.")

        elif data == "action_reset_cp":
            reset_checkpoint(engine.config.source_chat_id, engine.config.dest_chat_id)
            await query.answer("Progress reset! Will start from Message #1 on next run ♻️", show_alert=True)
            text = await build_forward_dashboard_text(engine, user_id)
            kb = build_forward_dashboard_keyboard(engine)
            await query.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    @bot.on_callback_query()
    async def handle_callback_query(_, query: CallbackQuery):
        try:
            await _process_callback_query(query)
        except MessageNotModified:
            await query.answer()
        except Exception as err:
            logger.error(f"Callback query handler error: {err}", exc_info=True)
            try:
                await query.answer(f"Error: {err}", show_alert=True)
            except Exception:
                pass
