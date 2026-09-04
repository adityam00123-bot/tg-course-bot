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
- **Factual & Sourced Responses Only**: The user explicitly dislikes "estimated" or "guessed" answers. When explaining limits (like Cloud Shell size, API rates), ALWAYS perform a web search to find the *exact* limit, and provide the answer *with sources*.
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
- **Dynamic Hardware Auto-Scaling**: The bot dynamically adapts to CPU cores and GPU availability. On 2 cores (Cloud Shell/Colab), it uses 2-3 downloaders and 8 MTProto workers. On 4+ cores (Kaggle), it scales to 4-6 downloaders, 16 MTProto workers, and NVENC GPU video encoding.

## 6. Telegram API Rate & Connection Limits (MTProto Specs)
- **Forwarding / Instant Copy**: Telegram allows ~2000 messages per hour. The bot uses a dynamic `2.0s` delay between messages (1800 msgs/hr) to stay perfectly under this limit.
- **Upload / Download Bandwidth & Connections**: Telegram's MTProto allows multi-part chunk streaming (4-8 TCP connections per file). The safe ceiling per account is **4 to 6 parallel file transfers (max 16 MTProto workers)**. Exceeding 16 simultaneous socket connections per session triggers DC-level TCP throttling (dropping speed to <1 MB/s) or aggressive FloodWait bans.
- **Auto-Pacing on Media**: Because uploading a 1GB file naturally takes 1-2 minutes, the 2.0s delay is skipped during media transfers, as the upload time itself acts as natural pacing.

## 8. Golden Architecture: Strict Sequential Turbo (Architecture A) — Golden Commit `cecd423`
- **Golden Commit Hash:** `cecd423` (September 3, 2026) — **PROVEN MAXIMUM STABILITY & SPEED BASELINE**.
- **Proven Live Performance Metrics (Kaggle Standard 4-vCPU):**
  - **13.53 GB migrated in 27m 55s** (Sustained Card Speed: **8.3 – 8.8 MB/s**, including all DL + UL + idle time).
  - **77 Media + 7 Text messages migrated with 0 ERRORS!**
  - Single-file Upload Speed: **20 to 34 MB/s** (e.g. 1.12 GB in 33s = 33.7 MB/s, 185 MB in 5s = 32.6 MB/s).
  - Single-file Download Speed: **12 to 28 MB/s**.
  - **ZERO `⚠️PAUSED` states** throughout the entire multi-gigabyte run.
- **Core Architecture Rules (DO NOT ALTER WITHOUT BENCHMARKING):**
  1. **Strict `slot.done` Producer-Consumer Lockstep:**
     - In `pipeline_producer`, `await slot.done.wait()` blocks the producer until the consumer has fully published the current message to the destination channel and unlinked all temporary files from disk.
     - Guarantees **exactly 1 message in-flight** at any millisecond across the bot.
     - 100% of bandwidth and CPU is dedicated to Download, then 100% to Upload. Zero lock contention, zero task interference.
  2. **Fresh Session Lifecycle Per File (Zero Zombie Sockets):**
     - In `fast_save_file`, 3-4 fresh dedicated media sessions are created on the home DC per file and **cleanly stopped in `finally:`**.
     - NEVER maintain a persistent global pool across files; idle sockets get dropped by NAT/firewalls, creating half-open "zombie" connections that cause multi-minute hangs.
  3. **Auto-Crawl Guard & Socket Circuit-Breaker:**
     - **Cumulative 45s Rolling Average Watchdog:** Tracks byte snapshots over a rolling 45s window. If the cumulative rolling average drops below 1.0 MB/s on a file >30MB (even with momentary spikes up to 4-6 MB/s), it cleanly aborts for a fresh reconnect.
     - **Per-Socket Bad-Apple Circuit Breaker:** If a single 512KB chunk takes >4.0s on any socket (<128 KB/s), that specific socket is background-restarted (`_safe_session_restart`) without failing the file or stalling the remaining 3 healthy sockets.
  4. **Step 1 Zero-Risk Optimizations & Smart Token Bucket:**
     - **Native Linux Kernel TCP Auto-Tuning:** Preserved Linux dynamic TCP window auto-tuning (`tcp_moderate_rcvbuf`) without manual `SO_RCVBUF` overrides.
     - **Rolling 60s Token Bucket Limiter:** Max 24 msgs/min sliding window. Chote files and single messages publish with **0.0s instant delay**, while large bursts of 24+ tiny messages are safely paced to eliminate `FLOOD_WAIT` risk.
     - **Static Thumbnail Cache:** Reuses the uploaded `InputFile` handle for `thumb.jpg` (1-hour TTL), saving 200–400ms per video (saves ~1.6 hours over 20k files).
     - **Native `.m4v` Pass-Through:** Bypasses FFmpeg disk remux for `.m4v`, eliminating 7–10s disk I/O on 1GB+ files.
     - **Dedicated Per-File Clean Auth Handshake:** Strictly creates fresh `Auth(self, dc_id).create()` per file, avoiding stale transport drops on foreign DCs.
  5. **Emergency Rollback Point:**
     - If future experiments ever degrade performance or introduce stalls, immediately revert to commit `22a8956`:
       `git reset --hard 22a8956`

## 9. High-Speed MTProto Engine: Zero-Freeze & High-Throughput Guidelines
- **Zero-Freeze on Completion (100% Finish):**
  - In both `fast_download_media` and `fast_save_file`, the exact millisecond `len(completed_parts) >= total_parts`:
    1. Set completion flag (`dl_done.set()` / `up_done.set()`).
    2. Immediately cancel all remaining worker tasks (`for w in workers: if not w.done(): w.cancel()`).
    3. Workers catch `asyncio.CancelledError` cleanly and exit.
    4. `await asyncio.gather(*workers, return_exceptions=True)` finishes in `<0.001s` instead of waiting for in-flight requests to time out.
- **Non-Blocking Session Teardown:**
  - In `finally:`, auxiliary session closures are scheduled in the background using `asyncio.create_task(_bg_stop(aux_sessions))`.
  - The function returns the file path or `InputFile` handle to `migration.py` in `0.0ms` without waiting 1.5–2.5s for MTProto ping cancellation and transport closing.
- **Direct Kernel Page-Cache Disk Writes (No Thread Choke):**
  - Pre-allocating files via `os.posix_fallocate` guarantees contiguous disk extents.
  - Workers write directly via `out_fp.seek(offset); out_fp.write(chunk_data)` inside `async with file_lock`.
  - **NEVER** wrap disk writes in `asyncio.to_thread` inside `async with file_lock`: thread scheduling latency and lock contention drops download speed from 80+ MB/s down to 4–7 MB/s!
- **Burst Pacing & Smooth Ticker:**
  - MTProto invokes use `retries=1` (with `timeout=8` on DL and `timeout=10` on UL). Momentary 1s server-side rate-limit pauses during 60–80 MB/s bursts are handled cleanly without socket destruction or retry backoffs.
  - The console ticker in `migration.py` uses an Exponential Moving Average (EMA: $0.7 \times \text{inst} + 0.3 \times \text{prev}$) and displays true overall speed at 100%, preventing visual drops to 4.5 MB/s.
- **Pyrogram `TCP.TIMEOUT = 60` Socket Protection:**
  - Upgraded Pyrogram's hardcoded 10s TCP read timeout to `60` seconds. Prevents Pyrogram's `recv_worker` from prematurely timing out and killing the socket when Telegram DC enforces a momentary rate-limit hold after high-speed bursts.
- **Dynamic 85 MB/s Flow Pacer (Strictly 2 Sockets):**
  - In `fast_download_media`, a lightweight rolling 1-second pacer limits peak throughput to ~85 MB/s.
  - If speed is naturally below 85 MB/s (e.g. 20–70 MB/s), it adds 0ms delay.
  - When line rate spikes to 100–120 MB/s, it micro-paces chunks to prevent Telegram DC server buffer overflow and TCP ZeroWindow 10-second hard freezes, delivering a **smooth, unbroken 75–85 MB/s continuous stream**.
- **120s Idle Stall Watchdog:**
  - `_dl_stall_watchdog` idle threshold is strictly set to `idle > 120.0s`, ensuring large 2GB transfers are never prematurely aborted or wiped.

## 7. Cloud Execution Strategy
1. **Primary: Kaggle Notebooks** (4 vCPU, 30 GB RAM, 2x T4 GPU, ~73 GB Disk). Allows "Save & Run All (Commit)" for 12-hour background execution without keeping the browser open. Weekly GPU quota is 30 hours.
2. **Fallback: Google Colab** (2 vCPU, 12.6 GB RAM, 1x T4 GPU, ~78 GB Disk). Used when Kaggle's 30-hour weekly GPU quota is exhausted. Requires keeping the browser tab active.
