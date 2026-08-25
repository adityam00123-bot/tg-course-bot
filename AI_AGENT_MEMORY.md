# AI Agent Memory & Project Architecture
**DO NOT DELETE OR MODIFY THIS FILE UNLESS ADDING NEW FEATURES**

This file serves as a persistent memory map for any AI Agent working on this repository (`tg-course-bot`). Read this first to understand what features are fully working, what constraints exist, and what the core architectural decisions are, so you **do not break existing functionality**.

## 1. Core Goal
A Telegram Content Migration Bot that copies content from restricted and unrestricted channels to a destination channel using a dual-client system (Bot API for UI/Commands, Pyrogram Userbot for content extraction).

## 2. Successfully Working Features (DO NOT BREAK)
- **Instant Server-Side Copy**: If no video modifications (watermark, thumbnails) are needed, the bot uses `self.client.send_video(..., video=msg.video.file_id)` or `forward_messages(drop_author=True)` for instantaneous, zero-download transfer.
- **Watermark & Custom Thumbnails**: Falls back to local download -> FFmpeg processing -> upload when enabled. 
- **Flood Wait Optimization**: Fixed a critical bug where redundant `get_me()` calls caused `users.GetFullUser` flood-wait delays (3-6 seconds per message). *Rule: NEVER add `get_me()` inside the migration loop (`_upload_and_post_media`). Use `self._resolve_peer_cached()` for peer lookups.*
- **FFmpeg Size Optimization**: Watermark FFmpeg command is optimized to `-preset veryfast -crf 26 -pix_fmt yuv420p -threads 0`. This prevents 130MB videos from inflating to 270MB.
- **Smart ZIP Auto-Extractor**: Downloads `.zip` files, extracts contents, and uploads each file natively (bypassing limits if needed).
- **Clean Old Watermark**: Uses FFmpeg `delogo` filter and brand cover to hide existing watermarks before applying the new one.
- **Auto-Resume**: Uses `migration_progress.json` to resume from the last message if the bot restarts.
- **OutputFormat**: Allows switching between `OutputFormat.VIDEO` (streamable MP4) and `OutputFormat.FILE` (Telegram document).
- **Session Management**: Userbot sessions are created locally via Pyrogram and stored as `.session` files. Memory cache `USER_CLIENTS` prevents redundant DB lookups.

## 3. Critical Constraints
- **Avoid Flood Waits**: Do NOT introduce new API calls per message if they are not absolutely necessary. Avoid `GetFullUser` inside loops.
- **GitHub Codespaces & Render**: The bot runs on GitHub Codespaces (during dev) and Render Free Tier (in production). Render has very low CPU resources (0.1 core). Keep FFmpeg processes as lightweight as possible.
- **Zero FFmpeg Overhead**: When watermark is disabled, the code must purely route through instant server-side copy. Do not accidentally force downloads.
- **Do not overwrite `OutputFormat` enum**: It must be imported correctly in `handlers.py` and `migration.py` from `migration.py` or a shared `config.py` depending on structure. (Currently located in `migration.py`).

## 4. Current State
As of August 25, 2026:
The codebase is highly optimized. Speed is back to 4-5 MB/s (and instant for unmodified files). Telemetry issues were resolved. The bot is stable.
