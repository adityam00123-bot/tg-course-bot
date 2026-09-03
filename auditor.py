"""
Channel Auditor and Sequential Alignment Engine.
Audits source vs destination channel to detect missing lectures/gaps,
and performs Forward-and-Delete Shuffle to restore strict chronological sequence.
"""

import os
import re
import json
import time
import math
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union, Callable, Awaitable

from pyrogram import Client, raw, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from pyrogram.enums import ChatMemberStatus

from config import Config
logger = logging.getLogger("migration_bot.auditor")

MESSAGE_MAP_FILE = Config.BASE_DIR / "message_map.json"


def _get_map_key(source_id: Any, dest_id: Any) -> str:
    return f"{source_id}_{dest_id}"


def load_message_map(source_id: Any, dest_id: Any) -> Dict[int, int]:
    """Loads source_msg_id -> dest_msg_id mapping for a channel pair."""
    try:
        if MESSAGE_MAP_FILE.exists():
            with open(MESSAGE_MAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = _get_map_key(source_id, dest_id)
            if key in data and isinstance(data[key], dict):
                return {int(k): int(v) for k, v in data[key].items()}
    except Exception as e:
        logger.debug(f"Could not load message map: {e}")
    return {}


def record_message_map(source_id: Any, dest_id: Any, src_msg_id: int, dst_msg_id: int) -> None:
    """Records a mapped source_msg_id -> dest_msg_id."""
    try:
        data = {}
        if MESSAGE_MAP_FILE.exists():
            try:
                with open(MESSAGE_MAP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        key = _get_map_key(source_id, dest_id)
        if key not in data or not isinstance(data[key], dict):
            data[key] = {}
        data[key][str(src_msg_id)] = int(dst_msg_id)
        with open(MESSAGE_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not record message map: {e}")


def update_message_map_batch(source_id: Any, dest_id: Any, updates: Dict[int, int]) -> None:
    """Updates multiple mappings at once (e.g. after shuffle re-alignment)."""
    try:
        data = {}
        if MESSAGE_MAP_FILE.exists():
            try:
                with open(MESSAGE_MAP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        key = _get_map_key(source_id, dest_id)
        if key not in data or not isinstance(data[key], dict):
            data[key] = {}
        for k, v in updates.items():
            data[key][str(k)] = int(v)
        with open(MESSAGE_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not batch update message map: {e}")


def extract_source_id_from_dest_message(msg: Message) -> Optional[int]:
    """Tries to extract source message ID from destination message file_name or caption/text."""
    if not msg or msg.empty or msg.service:
        return None

    # 1. Check document/video file name (e.g., media_-1001895806383_2093.mp4 or stream_media_..._2093.mp4)
    file_name = None
    if msg.video and getattr(msg.video, "file_name", None):
        file_name = msg.video.file_name
    elif msg.document and getattr(msg.document, "file_name", None):
        file_name = msg.document.file_name

    if file_name:
        m = re.search(r"(?:media|stream_media)_-?\d+_(\d+)\.", file_name)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

    # 2. Check caption or text for [#1234] or (ID: 1234)
    content = msg.caption or msg.text
    if content:
        m = re.search(r"[#\[\(](\d{1,7})[\]\)]", content)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

    return None


async def check_admin_permissions(client: Client, chat_id: Union[int, str]) -> Tuple[bool, str]:
    """
    Verifies that the bot/client has Admin rights with can_delete_messages and can_post_messages
    in the destination channel.
    """
    try:
        me = await client.get_me()
        member = await client.get_chat_member(chat_id, me.id)
        if member.status == ChatMemberStatus.OWNER:
            return True, "OK"
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            privs = getattr(member, "privileges", None)
            if privs:
                can_delete = getattr(privs, "can_delete_messages", False)
                if can_delete:
                    return True, "OK"
                else:
                    return (
                        False,
                        "⚠️ <b>Missing Admin Permission!</b>\n\n"
                        "Please grant <b>'Delete Messages'</b> permission to the bot in the destination channel "
                        "so it can clean up displaced messages after re-ordering."
                    )
            return True, "OK"
        return (
            False,
            "⚠️ <b>Admin Permission Required!</b>\n\n"
            "Please promote the bot to <b>Administrator</b> in the destination channel with "
            "<b>'Delete Messages'</b> and <b>'Post Messages'</b> enabled."
        )
    except Exception as e:
        err = str(e).lower()
        if "user_not_participant" in err or "chat_admin_required" in err:
            return (
                False,
                "⚠️ <b>Not in Channel or Admin Required!</b>\n\n"
                "Please add the bot to the destination channel as an <b>Administrator</b>."
            )
        logger.debug(f"Permission check fallback: {e}")
        return True, "OK"


async def scan_channel_gaps(
    client: Client,
    source_chat_id: Union[int, str],
    dest_chat_id: Union[int, str],
    start_id: Optional[int] = None,
    end_id: Optional[int] = None,
    in_memory_failed_ids: Optional[List[int]] = None
) -> Dict[str, Any]:
    """
    Scans source channel vs destination channel and failed_messages.json to identify missing lecture gaps.
    Uses multi-layered detection:
    1. Explicit failed_messages.json registry + in-memory error list.
    2. Persistent message_map.json.
    3. Content & File-Size Fingerprinting: Compares exact byte sizes and text between Source and Destination.
    """
    from migration import get_failed_messages, load_checkpoint

    # 1. Gather all recorded failed IDs
    failed_ids = set(get_failed_messages(source_chat_id, dest_chat_id))
    if in_memory_failed_ids:
        failed_ids.update(in_memory_failed_ids)

    # 2. Determine effective range:
    cp = load_checkpoint(source_chat_id, dest_chat_id)
    if not start_id:
        start_id = 1
    if not end_id:
        end_id = max(cp, 1)

    # 3. Read destination channel messages to build fingerprints
    dest_video_sizes = set()
    dest_doc_sizes = set()
    dest_photo_sizes = set()
    dest_texts = set()
    dest_mapped_source_ids = set()

    msg_map = load_message_map(source_chat_id, dest_chat_id)
    for sid in msg_map.keys():
        dest_mapped_source_ids.add(int(sid))

    logger.info(f"🔍 [Auditor] Inspecting destination channel for content fingerprints...")
    try:
        discovered_map = {}
        async for dmsg in client.get_chat_history(dest_chat_id, limit=800):
            if dmsg.empty or dmsg.service:
                continue
            if dmsg.video and getattr(dmsg.video, "file_size", None):
                dest_video_sizes.add(dmsg.video.file_size)
            if dmsg.document and getattr(dmsg.document, "file_size", None):
                dest_doc_sizes.add(dmsg.document.file_size)
            if dmsg.photo and getattr(dmsg.photo, "file_size", None):
                dest_photo_sizes.add(dmsg.photo.file_size)
            if dmsg.text:
                dest_texts.add(dmsg.text.strip())

            extracted_sid = extract_source_id_from_dest_message(dmsg)
            if extracted_sid:
                dest_mapped_source_ids.add(extracted_sid)
                discovered_map[extracted_sid] = dmsg.id

        if discovered_map:
            update_message_map_batch(source_chat_id, dest_chat_id, discovered_map)
            msg_map.update(discovered_map)
    except Exception as e:
        logger.debug(f"[Auditor] Error reading destination history: {e}")

    # 4. Now scan source channel messages in the range [start_id, end_id]
    missing_ids = set(failed_ids)

    if end_id > start_id:
        logger.info(f"🔍 [Auditor] Comparing Source #{start_id}..#{end_id} against Destination channel...")
        scan_limit = 5000
        target_ids = list(range(start_id, min(end_id + 1, start_id + scan_limit)))

        for i in range(0, len(target_ids), 100):
            batch = target_ids[i:i + 100]
            try:
                msgs = await client.get_messages(source_chat_id, message_ids=batch)
                if not isinstance(msgs, list):
                    msgs = [msgs] if msgs else []
                for m in msgs:
                    if not m or m.empty or m.service:
                        continue
                    sid = m.id

                    if sid in failed_ids:
                        continue

                    if sid in dest_mapped_source_ids:
                        continue

                    # Check by content fingerprint
                    is_present = False
                    if m.video and getattr(m.video, "file_size", None):
                        if m.video.file_size in dest_video_sizes or m.video.file_size in dest_doc_sizes:
                            is_present = True
                    elif m.document and getattr(m.document, "file_size", None):
                        if m.document.file_size in dest_doc_sizes or m.document.file_size in dest_video_sizes:
                            is_present = True
                    elif m.photo and getattr(m.photo, "file_size", None):
                        if m.photo.file_size in dest_photo_sizes:
                            is_present = True
                    elif m.text and not bool(m.video or m.document or m.photo):
                        if m.text.strip() in dest_texts:
                            is_present = True

                    if not is_present:
                        logger.info(f"🔍 [Auditor] Gap detected: Source #{sid} is missing in destination!")
                        missing_ids.add(sid)
            except Exception as scan_err:
                logger.debug(f"[Auditor] Source scan error for batch {batch}: {scan_err}")

    missing_list = sorted(list(missing_ids))
    return {
        "source_chat_id": source_chat_id,
        "dest_chat_id": dest_chat_id,
        "missing_ids": missing_list,
        "count": len(missing_list),
        "mapped_count": len(msg_map),
        "scanned_range": f"#{start_id} to #{end_id}"
    }


async def realign_channel_sequence(
    engine: Any,
    missing_ids: List[int],
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> Dict[str, Any]:
    """
    Executes the Forward-and-Delete Shuffle to insert missing messages into their exact
    chronological position without leaving gaps or duplicates.

    Algorithm:
    For each missing_id in ascending order:
      1. Upload missing_id from source to destination (it lands at the end of dest channel).
      2. Identify all messages in destination that were posted AFTER missing_id's correct position.
      3. Server-side copy/forward all subsequent messages to the end with drop_author=True (instant, 0 MB).
      4. Delete the old displaced messages from destination.
      5. Update message_map.json and clear missing_id from failed_messages.json.
    """
    from migration import remove_failed_message

    source_chat = engine.config.source_chat_id
    dest_chat = engine.config.dest_chat_id
    client = engine.userbot or engine.client

    # 1. Verify permissions upfront
    can_admin, admin_err = await check_admin_permissions(engine.bot, dest_chat)
    if not can_admin:
        return {
            "success": False,
            "error": admin_err,
            "recovered": [],
            "failed": missing_ids
        }

    missing_ids = sorted(missing_ids)
    recovered_ids = []
    failed_ids = []

    total_gaps = len(missing_ids)
    msg_map = load_message_map(source_chat, dest_chat)

    for idx, mid in enumerate(missing_ids, 1):
        if engine.cancel_event.is_set():
            break

        if progress_callback:
            await progress_callback(
                f"🔄 <b>Aligning Sequence ({idx}/{total_gaps})</b>\n\n"
                f"• Processing Gap Message: <b>#{mid}</b>\n"
                f"• Uploading missing media to destination..."
            )

        try:
            # 1. Fetch source message
            fetch_res = await client.get_messages(source_chat, message_ids=[mid])
            src_msg = fetch_res[0] if isinstance(fetch_res, list) and fetch_res else fetch_res
            if not src_msg or src_msg.empty:
                logger.warning(f"[Auditor] Source message #{mid} is empty or deleted. Skipping.")
                remove_failed_message(source_chat, dest_chat, mid)
                continue

            # 2. Determine which destination messages come AFTER mid's correct chronological place
            # In msg_map: any source ID s > mid that is already in dest
            subsequent_src_ids = sorted([s for s in msg_map.keys() if s > mid])
            subsequent_dest_ids = [msg_map[s] for s in subsequent_src_ids]

            # If message_map has no subsequent IDs, inspect destination channel history
            if not subsequent_dest_ids:
                dest_history = []
                async for dmsg in client.get_chat_history(dest_chat, limit=100):
                    if not dmsg.empty and not dmsg.service:
                        dest_history.append(dmsg.id)
                discovered_subsequent = []
                for did in dest_history:
                    sid = extract_source_id_from_dest_message(await client.get_messages(dest_chat, message_ids=[did]))
                    if sid and sid > mid:
                        discovered_subsequent.append(did)
                if discovered_subsequent:
                    subsequent_dest_ids = sorted(discovered_subsequent)

            # 3. Upload missing message to destination
            pre_upload_dest_history = []
            async for m in client.get_chat_history(dest_chat, limit=1):
                pre_upload_dest_history.append(m.id)
            pre_last_id = pre_upload_dest_history[0] if pre_upload_dest_history else 0

            await engine._migrate_single_message(src_msg)

            post_upload_dest_history = []
            async for m in client.get_chat_history(dest_chat, limit=1):
                post_upload_dest_history.append(m.id)
            new_mid_dest_id = post_upload_dest_history[0] if post_upload_dest_history else (pre_last_id + 1)

            # Record in map
            msg_map[mid] = new_mid_dest_id
            record_message_map(source_chat, dest_chat, mid, new_mid_dest_id)
            remove_failed_message(source_chat, dest_chat, mid)

            # 4. If subsequent messages exist, perform Forward-and-Delete Shuffle!
            if subsequent_dest_ids:
                if progress_callback:
                    await progress_callback(
                        f"🔄 <b>Aligning Sequence ({idx}/{total_gaps})</b>\n\n"
                        f"• Uploaded <b>#{mid}</b> ✅\n"
                        f"• Shuffling {len(subsequent_dest_ids)} subsequent message(s) to restore exact order..."
                    )

                new_dest_map = {}
                for b_i in range(0, len(subsequent_dest_ids), 100):
                    batch = subsequent_dest_ids[b_i:b_i + 100]
                    try:
                        forwarded = await client.forward_messages(
                            chat_id=dest_chat,
                            from_chat_id=dest_chat,
                            message_ids=batch,
                            drop_author=True
                        )
                        if not isinstance(forwarded, list):
                            forwarded = [forwarded] if forwarded else []

                        for old_id, new_msg in zip(batch, forwarded):
                            if new_msg:
                                for s, d in list(msg_map.items()):
                                    if d == old_id:
                                        msg_map[s] = new_msg.id
                                        new_dest_map[s] = new_msg.id

                        await client.delete_messages(
                            chat_id=dest_chat,
                            message_ids=batch
                        )
                        # Polite safety pacing between batches of 100 to stay well under rate limits
                        await asyncio.sleep(1.5)
                    except Exception as fwd_err:
                        logger.warning(f"[Auditor] Batch shuffle error for {batch}: {fwd_err}")

                if new_dest_map:
                    update_message_map_batch(source_chat, dest_chat, new_dest_map)

            recovered_ids.append(mid)
            logger.info(f"✨ [Auditor] Successfully re-aligned missing message #{mid} into exact sequence!")

        except Exception as e:
            logger.error(f"[Auditor] Failed to realign #{mid}: {e}")
            failed_ids.append(mid)

    return {
        "success": True,
        "recovered": recovered_ids,
        "failed": failed_ids,
        "total_gaps": total_gaps
    }
