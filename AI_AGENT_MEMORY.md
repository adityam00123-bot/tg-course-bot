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
- **Concurrent 3-Stage Pipeline**: When watermark/thumbnails are enabled, the bot uses a concurrent pipeline (`Download` -> `Watermark` -> `Upload`). It uses `asyncio.Semaphore` to run multiple downloads (max 3) and FFmpeg jobs (max CPU cores) in the background while maintaining strictly sequential uploads to Telegram.
- **Dynamic Disk Budgeting**: The pipeline dynamically calculates free disk space (using 60% of available space) to decide how many videos to prefetch, preventing "disk out of space" errors when handling huge files.
- **Fast MTProto Uploader / Downloader**: The bot uses a custom `fast_uploader.py` to maximize bandwidth. It overrides Pyrogram's native transmission engine to use multiple concurrent TCP connections (`max_concurrent_transmissions=8`), utilizing system resources efficiently for lightning-fast speeds.

## 3. Critical Constraints
- **Avoid Flood Waits**: Do NOT introduce new API calls per message if they are not absolutely necessary. Avoid `GetFullUser` inside loops.
- **GitHub Codespaces & Render**: The bot runs on GitHub Codespaces (during dev) and Render Free Tier (in production). Render has very low CPU resources (0.1 core). Keep FFmpeg processes as lightweight as possible.
- **Zero FFmpeg Overhead**: When watermark is disabled, the code must purely route through instant server-side copy. Do not accidentally force downloads.
- **Sequential Uploads are Mandatory**: Telegram uploads MUST happen in original sequence. The pipeline handles this by awaiting a slot's `ready` event in order. DO NOT parallelize the final `send_video` step.
- **Do not overwrite `OutputFormat` enum**: It must be imported correctly in `handlers.py` and `migration.py` from `migration.py` or a shared `config.py` depending on structure. (Currently located in `migration.py`).

## 4. Current State
As of August 26, 2026:
The codebase is highly optimized with a new concurrent processing pipeline for watermarking. Speed is significantly faster as downloads and processing happen concurrently while uploads happen sequentially. The bot is stable and adapts dynamically to disk/CPU limits.
## 5. Recent Architecture Upgrades (Aug 2026)
- **Dynamic Smart Pacing (Anti-FloodWait)**: Implemented in the pipeline consumer. The bot enforces a strict 2.0s delay between publishing messages to the destination. If processing a file takes >2.0s, it skips the delay. This prevents Telegram's aggressive 250s+ FloodWaits when dumping messages in Turbo Mode.
- **Exact FloodWait Penalties**: Removed the Config.FLOOD_WAIT_MAX_SLEEP cap in _execute_with_flood_retry. The bot now respects and logs the *exact* penalty seconds issued by Telegram (e.g. 1500s). Do not artificially cap this, otherwise the bot will loop and spam the API during a ban.
- **Dynamic Pipeline Detection**: The bot dynamically checks has_protected_content, enable_watermark, and enable_custom_thumbnail. If ALL are false (e.g. unrestricted channel with no editing), it disables the pipeline and uses Instant Server Copy (0s download time).
- **Massive 4GB File Handling (Escape Hatch)**: The `_pipeline_prefetch` uses a smart Sliding Window to allocate disk budget (60% of free space). For massive 4GB files that exceed the budget, an *Escape Hatch* allows them to proceed exclusively (using up to 100% of free space) as long as no other downloads are running. This prevents deadlocks.

## 6. Telegram API Rate Limits
- **Forwarding / Instant Copy**: Telegram allows ~2000 messages per hour. The bot uses a dynamic `2.0s` delay between messages (1800 msgs/hr) to stay perfectly under this limit.
- **Upload / Download (Media)**: Uploading and downloading large files is limited by bandwidth (~20-30 MB/s). Because uploading a 1GB file naturally takes 1-2 minutes, the 2.0s delay is often skipped during media transfers, as the time taken to upload itself acts as the pacing.
