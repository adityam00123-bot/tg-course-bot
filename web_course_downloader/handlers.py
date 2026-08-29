"""
Telegram Bot Handlers & Conversation UI for Web Course Downloader.
"""

import os
import re
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

from config import Config
from extractors.classplus import ClassplusAPI
from extractors.generic_stream import GenericStreamExtractor
from downloader import CourseDownloader
from uploader import TelegramCourseUploader

logger = logging.getLogger("bot_handlers")

# In-memory user state & session store
USER_SESSIONS: Dict[int, Dict[str, Any]] = {}


def get_user_session(user_id: int) -> Dict[str, Any]:
    if user_id not in USER_SESSIONS:
        USER_SESSIONS[user_id] = {
            "state": "IDLE",
            "classplus": ClassplusAPI(),
            "org_code": None,
            "org_id": None,
            "mobile": None,
            "session_id": None,
            "dest_chat_id": user_id  # Default destination is the user themselves
        }
    return USER_SESSIONS[user_id]


def register_bot_handlers(app: Client):
    """Register all bot message and callback query handlers."""

    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start(client: Client, message: Message):
        user_id = message.from_user.id
        sess = get_user_session(user_id)
        sess["state"] = "IDLE"

        text = (
            "🎓 <b>Welcome to CourseVerse Web & LMS Downloader!</b>\n\n"
            "Download clean, watermark-free video lectures, HLS streams, and PDF notes directly into Telegram.\n\n"
            "<b>✨ Supported Platforms:</b>\n"
            " • <b>Classplus LMS</b> (Full Batches & Store Courses)\n"
            " • <b>Generic Streams</b> (HLS / m3u8, MPD, MP4, PW, Vimeo, YouTube)\n"
            " • <b>PDF Study Materials</b>\n\n"
            "<i>Select an option below to begin:</i>"
        )

        buttons = [
            [
                InlineKeyboardButton("🔐 Classplus Login (OTP)", callback_data="cp_login"),
                InlineKeyboardButton("🔑 Direct Token Login", callback_data="cp_token_login")
            ],
            [
                InlineKeyboardButton("📚 My Enrolled Courses", callback_data="cp_my_courses"),
                InlineKeyboardButton("⚡ Direct Link Downloader", callback_data="direct_link_mode")
            ],
            [
                InlineKeyboardButton("🎯 Set Destination Channel", callback_data="set_dest_channel")
            ]
        ]

        await message.reply_text(
            text=text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    @app.on_callback_query()
    async def handle_callbacks(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        sess = get_user_session(user_id)
        data = query.data

        if data == "cp_login":
            sess["state"] = "AWAITING_ORG_CODE"
            await query.message.edit_text(
                "🏢 <b>Classplus Login - Step 1:</b>\n\n"
                "Please enter the <b>Organization Code</b> (e.g. <code>abcd</code>, <code>xyz</code>):",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cmd_cancel")]])
            )

        elif data == "cp_token_login":
            sess["state"] = "AWAITING_JWT_TOKEN"
            await query.message.edit_text(
                "🔑 <b>Direct Student Token Login:</b>\n\n"
                "Send your Classplus <code>x-access-token</code> (JWT Bearer Token):",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cmd_cancel")]])
            )

        elif data == "direct_link_mode":
            sess["state"] = "AWAITING_DIRECT_URL"
            await query.message.edit_text(
                "⚡ <b>Direct Link Downloader:</b>\n\n"
                "Send any <b>m3u8 stream link, direct video URL, or PDF link</b>:",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cmd_cancel")]])
            )

        elif data == "set_dest_channel":
            sess["state"] = "AWAITING_DEST_CHANNEL"
            await query.message.edit_text(
                "🎯 <b>Set Destination Channel:</b>\n\n"
                "Send the <b>Channel ID</b> (e.g. <code>-1004317253896</code>) or forward any message from your target channel.\n\n"
                "<i>Ensure the bot is an Admin with post permissions in the channel!</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cmd_cancel")]])
            )

        elif data == "cp_my_courses":
            cp: ClassplusAPI = sess["classplus"]
            if not cp.token:
                await query.answer("⚠️ Please login first!", show_alert=True)
                return

            await query.message.edit_text("⏳ <i>Fetching your enrolled courses & batches...</i>", parse_mode=enums.ParseMode.HTML)
            try:
                courses = await cp.get_enrolled_courses()
                batches = await cp.get_enrolled_batches()
                all_items = courses + batches

                if not all_items:
                    await query.message.edit_text(
                        "⚠️ <b>No enrolled courses or batches found on this account.</b>",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="cmd_start_menu")]])
                    )
                    return

                buttons = []
                for item in all_items[:20]:  # Up to 20 items per page
                    btn_text = f"📖 {item['title'][:35]}"
                    cb_data = f"cp_course_{item['id']}"
                    buttons.append([InlineKeyboardButton(btn_text, callback_data=cb_data)])

                buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="cmd_start_menu")])

                await query.message.edit_text(
                    f"📚 <b>Found {len(all_items)} Course(s) & Batch(es):</b>\n\n<i>Click to open contents:</i>",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
            except Exception as e:
                await query.message.edit_text(f"❌ Error fetching courses: {e}")

        elif data.startswith("cp_course_"):
            course_id = int(data.replace("cp_course_", ""))
            cp: ClassplusAPI = sess["classplus"]
            await query.message.edit_text("⏳ <i>Loading course curriculum...</i>", parse_mode=enums.ParseMode.HTML)

            contents = await cp.get_folder_contents(course_id=course_id, folder_id=0)
            buttons = []

            for f in contents["folders"]:
                buttons.append([InlineKeyboardButton(f"📁 {f['name'][:35]}", callback_data=f"cp_fld_{course_id}_{f['id']}")])

            if contents["videos"] or contents["pdfs"]:
                buttons.append([InlineKeyboardButton(f"⬇️ Download All ({len(contents['videos'])} Videos, {len(contents['pdfs'])} PDFs)", callback_data=f"cp_dlall_{course_id}_0")])

            buttons.append([InlineKeyboardButton("🔙 Back to Courses", callback_data="cp_my_courses")])

            await query.message.edit_text(
                f"📂 <b>Course Contents:</b>\n\n"
                f"• Folders: {len(contents['folders'])}\n"
                f"• Video Lectures: {len(contents['videos'])}\n"
                f"• Study PDFs: {len(contents['pdfs'])}\n",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        elif data.startswith("cp_fld_"):
            parts = data.split("_")
            course_id = int(parts[2])
            folder_id = int(parts[3])
            cp: ClassplusAPI = sess["classplus"]

            await query.message.edit_text("⏳ <i>Opening folder...</i>", parse_mode=enums.ParseMode.HTML)
            contents = await cp.get_folder_contents(course_id=course_id, folder_id=folder_id)

            buttons = []
            for f in contents["folders"]:
                buttons.append([InlineKeyboardButton(f"📁 {f['name'][:35]}", callback_data=f"cp_fld_{course_id}_{f['id']}")])

            for v in contents["videos"][:15]:
                buttons.append([InlineKeyboardButton(f"▶️ {v['name'][:30]}", callback_data=f"cp_dlone_v_{course_id}_{v['id']}")])

            for p in contents["pdfs"][:10]:
                buttons.append([InlineKeyboardButton(f"📄 {p['name'][:30]}", callback_data=f"cp_dlone_p_{course_id}_{p['id']}")])

            if contents["videos"] or contents["pdfs"]:
                buttons.append([InlineKeyboardButton(f"⬇️ Download All In Folder ({len(contents['videos'])} Videos, {len(contents['pdfs'])} PDFs)", callback_data=f"cp_dlall_{course_id}_{folder_id}")])

            buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"cp_course_{course_id}")])

            await query.message.edit_text(
                f"📁 <b>Folder Contents:</b>\n\n"
                f"• Subfolders: {len(contents['folders'])}\n"
                f"• Videos: {len(contents['videos'])}\n"
                f"• PDFs: {len(contents['pdfs'])}\n",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        elif data.startswith("cp_dlall_"):
            parts = data.split("_")
            course_id = int(parts[2])
            folder_id = int(parts[3])
            cp: ClassplusAPI = sess["classplus"]
            dest_chat = sess["dest_chat_id"]

            await query.message.edit_text("🚀 <b>Starting batch download & stream pipeline...</b>\n\n<i>Check destination channel for updates!</i>", parse_mode=enums.ParseMode.HTML)
            asyncio.create_task(run_batch_download(client, cp, course_id, folder_id, dest_chat, query.message))

        elif data == "cmd_cancel" or data == "cmd_start_menu":
            sess["state"] = "IDLE"
            await cmd_start(client, query.message)

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel"]))
    async def handle_user_input(client: Client, message: Message):
        user_id = message.from_user.id
        sess = get_user_session(user_id)
        state = sess.get("state", "IDLE")
        text = message.text.strip()

        if state == "AWAITING_ORG_CODE":
            cp: ClassplusAPI = sess["classplus"]
            status_msg = await message.reply_text("🔍 <i>Verifying Org Code...</i>", parse_mode=enums.ParseMode.HTML)
            res = await cp.verify_org_code(text)
            if res.get("success"):
                sess["org_code"] = text
                sess["org_id"] = res.get("org_id")
                sess["state"] = "AWAITING_MOBILE"
                await status_msg.edit_text(
                    f"✅ <b>Organization Found:</b> <code>{res.get('org_name')}</code>\n\n"
                    f"📱 <b>Step 2:</b> Enter your registered <b>10-digit Mobile Number</b>:",
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await status_msg.edit_text(f"❌ <b>Error:</b> {res.get('error')}\n\nPlease check the Org Code and try again.")

        elif state == "AWAITING_MOBILE":
            cp: ClassplusAPI = sess["classplus"]
            mobile = re.sub(r"\D", "", text)
            if len(mobile) < 10:
                await message.reply_text("⚠️ Please enter a valid 10-digit mobile number.")
                return

            status_msg = await message.reply_text("📩 <i>Sending OTP...</i>", parse_mode=enums.ParseMode.HTML)
            res = await cp.send_otp(mobile=mobile, org_id=sess["org_id"])
            if res.get("success"):
                sess["mobile"] = mobile
                sess["session_id"] = res.get("session_id")
                sess["state"] = "AWAITING_OTP"
                await status_msg.edit_text(
                    f"📨 <b>OTP Sent Successfully to +91 {mobile[:3]}****{mobile[-3:]}!</b>\n\n"
                    f"🔑 <b>Step 3:</b> Enter the <b>4 or 6-digit OTP</b> received on your phone:",
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await status_msg.edit_text(f"❌ <b>Failed to send OTP:</b> {res.get('error')}")

        elif state == "AWAITING_OTP":
            cp: ClassplusAPI = sess["classplus"]
            otp = re.sub(r"\D", "", text)
            status_msg = await message.reply_text("🔐 <i>Verifying OTP & Logging In...</i>", parse_mode=enums.ParseMode.HTML)
            res = await cp.verify_otp(
                mobile=sess["mobile"],
                otp=otp,
                org_id=sess["org_id"],
                session_id=sess["session_id"]
            )
            if res.get("success"):
                sess["state"] = "IDLE"
                await status_msg.edit_text(
                    f"🎉 <b>Login Successful!</b>\n\n"
                    f"👤 <b>Student Name:</b> {res.get('user_name')}\n\n"
                    f"<i>Click below to view your enrolled courses:</i>",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📚 View My Courses", callback_data="cp_my_courses")],
                        [InlineKeyboardButton("🔙 Main Menu", callback_data="cmd_start_menu")]
                    ])
                )
            else:
                await status_msg.edit_text(f"❌ <b>Login Failed:</b> {res.get('error')}\n\nPlease check the OTP and try again.")

        elif state == "AWAITING_JWT_TOKEN":
            cp: ClassplusAPI = sess["classplus"]
            cp.set_token(text)
            sess["state"] = "IDLE"
            await message.reply_text(
                "✅ <b>Student Access Token Saved!</b>\n\n<i>You can now access all your enrolled courses directly.</i>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📚 Open Courses", callback_data="cp_my_courses")]
                ])
            )

        elif state == "AWAITING_DEST_CHANNEL":
            try:
                dest_id = int(text) if (text.startswith("-") or text.isdigit()) else text
                sess["dest_chat_id"] = dest_id
                sess["state"] = "IDLE"
                await message.reply_text(
                    f"🎯 <b>Destination Channel Set:</b> <code>{dest_id}</code>\n\n"
                    f"<i>All future lecture downloads will be automatically posted here!</i>",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="cmd_start_menu")]])
                )
            except Exception as e:
                await message.reply_text(f"⚠️ Invalid Channel ID: {e}")

        elif state == "AWAITING_DIRECT_URL" or text.startswith(("http://", "https://")):
            sess["state"] = "IDLE"
            url = text
            status_msg = await message.reply_text("⚡ <i>Resolving stream / file URL...</i>", parse_mode=enums.ParseMode.HTML)
            dest_chat = sess.get("dest_chat_id", user_id)
            asyncio.create_task(run_direct_url_download(client, url, dest_chat, status_msg))


async def run_direct_url_download(client: Client, url: str, dest_chat: int | str, status_msg: Message):
    """Download single stream URL and post to destination channel."""
    try:
        if url.lower().endswith(".pdf") or "pdf" in url.lower():
            # PDF Download
            target_path = Config.DOWNLOAD_DIR / f"material_{int(time.time())}.pdf"
            await status_msg.edit_text("📄 <i>Downloading PDF Document...</i>", parse_mode=enums.ParseMode.HTML)
            dl_path = await CourseDownloader.download_direct_file(url, target_path)
            if dl_path and dl_path.exists():
                await status_msg.edit_text("🔼 <i>Uploading PDF to Telegram...</i>", parse_mode=enums.ParseMode.HTML)
                await TelegramCourseUploader.upload_document(
                    client=client,
                    chat_id=dest_chat,
                    doc_path=dl_path,
                    caption="📄 <b>Study Notes Document</b>\n\n<i>Powered by CourseVerse Downloader</i>"
                )
                try: dl_path.unlink()
                except Exception: pass
                await status_msg.edit_text("✅ <b>PDF Download & Upload Complete!</b>", parse_mode=enums.ParseMode.HTML)
            else:
                await status_msg.edit_text("❌ Failed to download PDF document.")
            return

        # Video / m3u8 Stream
        info = GenericStreamExtractor.extract_info(url)
        title = info.get("title", "Video Lecture")
        stream_url = info.get("stream_url", url)

        target_video = Config.DOWNLOAD_DIR / f"lecture_{int(time.time())}.mp4"
        await status_msg.edit_text(f"🎬 <b>Downloading Video Stream:</b> <i>{title}</i>\n\n⏳ <i>Extracting clean stream...</i>", parse_mode=enums.ParseMode.HTML)

        if ".m3u8" in stream_url:
            dl_path = await CourseDownloader.download_m3u8_stream(stream_url, target_video)
        else:
            dl_path = await CourseDownloader.download_direct_file(stream_url, target_video)

        if dl_path and dl_path.exists():
            await status_msg.edit_text("🔼 <i>Uploading clean video to Telegram Channel...</i>", parse_mode=enums.ParseMode.HTML)
            thumb = Config.DEFAULT_THUMB if Config.DEFAULT_THUMB.exists() else None
            caption = f"🎬 <b>{title}</b>\n\n<i>✨ Clean Stream • Powered by CourseVerse</i>"

            await TelegramCourseUploader.upload_video(
                client=client,
                chat_id=dest_chat,
                video_path=dl_path,
                caption=caption,
                thumb_path=thumb,
                duration=info.get("duration", 0)
            )
            try: dl_path.unlink()
            except Exception: pass
            await status_msg.edit_text(f"✅ <b>Uploaded:</b> <i>{title}</i> -> Destination Channel!", parse_mode=enums.ParseMode.HTML)
        else:
            await status_msg.edit_text("❌ Failed to extract/download video stream.")
    except Exception as e:
        logger.error(f"Direct stream download error: {e}")
        await status_msg.edit_text(f"❌ Error: {e}")


async def run_batch_download(client: Client, cp: ClassplusAPI, course_id: int, folder_id: int, dest_chat: int | str, status_msg: Message):
    """Download full batch / folder of videos and PDFs sequentially and post to channel."""
    try:
        contents = await cp.get_folder_contents(course_id=course_id, folder_id=folder_id)
        videos = contents["videos"]
        pdfs = contents["pdfs"]
        total = len(videos) + len(pdfs)
        current = 0

        for idx, v in enumerate(videos, 1):
            current += 1
            v_name = v.get("name", f"Lecture {idx}")
            v_url = v.get("url")

            if not v_url:
                continue

            await status_msg.edit_text(
                f"🔽 <b>[{current}/{total}] Downloading Video:</b>\n"
                f"🎬 <i>{v_name}</i>",
                parse_mode=enums.ParseMode.HTML
            )

            target_path = Config.DOWNLOAD_DIR / f"cp_vid_{course_id}_{v.get('id')}.mp4"
            if ".m3u8" in v_url:
                dl_res = await CourseDownloader.download_m3u8_stream(v_url, target_path)
            else:
                dl_res = await CourseDownloader.download_direct_file(v_url, target_path)

            if dl_res and dl_res.exists():
                thumb = Config.DEFAULT_THUMB if Config.DEFAULT_THUMB.exists() else None
                caption = f"🎬 <b>{v_name}</b>\n\n<i>✨ Clean Stream • CourseVerse</i>"
                await TelegramCourseUploader.upload_video(
                    client=client,
                    chat_id=dest_chat,
                    video_path=dl_res,
                    caption=caption,
                    thumb_path=thumb,
                    duration=v.get("duration", 0)
                )
                try: dl_res.unlink()
                except Exception: pass

        for idx, p in enumerate(pdfs, 1):
            current += 1
            p_name = p.get("name", f"Notes {idx}.pdf")
            p_url = p.get("url")

            if not p_url:
                continue

            await status_msg.edit_text(
                f"🔽 <b>[{current}/{total}] Downloading PDF Notes:</b>\n"
                f"📄 <i>{p_name}</i>",
                parse_mode=enums.ParseMode.HTML
            )

            target_path = Config.DOWNLOAD_DIR / f"cp_doc_{course_id}_{p.get('id')}.pdf"
            dl_res = await CourseDownloader.download_direct_file(p_url, target_path)
            if dl_res and dl_res.exists():
                caption = f"📄 <b>{p_name}</b>\n\n<i>✨ Course Study Material • CourseVerse</i>"
                await TelegramCourseUploader.upload_document(
                    client=client,
                    chat_id=dest_chat,
                    doc_path=dl_res,
                    caption=caption,
                    file_name=p_name
                )
                try: dl_res.unlink()
                except Exception: pass

        await status_msg.edit_text(
            f"🎉 <b>Batch Complete!</b>\n\n"
            f"✅ Successfully migrated {len(videos)} videos and {len(pdfs)} study notes to destination channel!",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Batch download pipeline error: {e}")
        await status_msg.edit_text(f"❌ Batch error: {e}")
