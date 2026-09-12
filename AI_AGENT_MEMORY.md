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

## 8. Golden Architecture: Strict Sequential Turbo (Architecture A) — Golden Commit `2e2cbc5`
- **Official All-Time Golden Commit:** `2e2cbc5` (September 5, 2026) — **PROVEN RECORD-BREAKER BASELINE (174+ GB in a single day with 0 ERRORS)**.
- **Proven Live Performance Metrics (Kaggle Standard 4-vCPU, Taiwan & Europe IPs):**
  - **Run 1 (Taiwan IP):** 110.64 GB (555 Media + 70 Text) in 3h 43m 47s with **0 ERRORS!**
  - **Run 2 (Europe IP):** 51.09 GB (193 Media + 44 Text) in 2h 57m 50s with **0 ERRORS!**
  - **Run 3 (Night Taiwan Run):** 12.24 GB in 20m (10.4 MB/s sustained card speed = ~37.4 GB/hr) with **0 ERRORS!**
  - **Total 24h Data Transferred:** Over **174+ GB migrated** flawlessly, completely breaking all historical repository records.
  - **Massive 2.0 GB Video Transfers:** Sustained 20–35 MB/s line rate (e.g. #4591 1999.9 MB DL in 1m 02s @ 32.2 MB/s, UL in 1m 18s @ 25.4 MB/s).
  - **Pause Resilience:** Successfully survived DC burst rate-limit pauses (`⚡ ⬇️ DL: #4600 @ ⚠️PAUSED`) with 0 crashes, auto-rotating chunks and recovering instantly to 43.1 MB/s.
  - **Free Disk Steady-State:** Maintained rock-steady at 19.5 GB free disk throughout multi-hour runs via active 10-message GC and orphan purge.
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
     - If future experiments ever degrade performance or introduce stalls, immediately revert to golden commit `2e2cbc5`:
       `git reset --hard 2e2cbc5`

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

## 7. Cloud Execution Strategy
1. **Primary: Kaggle Notebooks** (4 vCPU, 30 GB RAM, 2x T4 GPU, ~73 GB Disk). Allows "Save & Run All (Commit)" for 12-hour background execution without keeping the browser open. Weekly GPU quota is 30 hours.
2. **Fallback: Google Colab** (2 vCPU, 12.6 GB RAM, 1x T4 GPU, ~78 GB Disk). Used when Kaggle's 30-hour weekly GPU quota is exhausted. Requires keeping the browser tab active.

## 10. 12-Hour Continuous Migration Resilience Architecture (September 2026)
- **Problem Statement:** Long migrations (>3–6 hours or >60–120 GB) historically hit error cascades due to:
  1. Telegram's 2.5–3.0 hour `file_reference` hard expiration.
  2. Kaggle's ~73 GB storage ceiling and Linux unlinked inode buffering.
  3. Linux File Descriptor (FD) exhaustion (`ulimit -n 1024`) from thousands of sockets entering `TIME_WAIT`.
  4. MTProto session salt and transport staleness after 6 hours of continuous data flow.
## 11. Degradation Cascade Elimination & Non-Stop Migration Architecture (Commit `5e7ac42`+)
- **The Degradation Cascade Root Cause (Observed on `#5002–#5009` after 4.5h & >100 GB):**
  1. **Telegram Token Bucket Emptying:** MTProto servers enforce an edge token bucket (~120–150 MB). Bursting at 65 MB/s empties this bucket in ~2s.
  2. **Telegram Refill Window (40–60s):** The Telegram DC pauses packet delivery for 40–60 seconds to refill tokens.
  3. **The Fatal 45s Watchdog & Unlink:** The download watchdog killed the task at 45s. On retry, line 1268 unlinked `temp_target` and `fast_download_media` wiped the file from byte 0. It re-downloaded to 130 MB, paused, got aborted at 45s, repeated 4 times, and failed the file!
  4. **Upload Rolling-Average False Abort:** The upload watchdog aborted if rolling average was `<1.0 MB/s` after 35s. During a DC ACK pause, progress is 0, so rolling speed is 0.0 MB/s, causing premature aborts and restarting parts from 0.
  5. **Closed TCPTransport Race:** When a session restarted, concurrent workers invoking on the unstarted transport crashed with `unable to perform operation on <TCPTransport closed=True reading=False>; the handler is closed`.
- **The 4 Permanent Architectural Safeguards:**
  1. **Chunk-Level Resume via `.parts` Bitmask:** `fast_download_media` creates a companion binary file (`<path>.parts`). Completed 1MB parts are recorded in real-time. If interrupted, retry inspects `.parts`, keeps all previously downloaded bytes on disk, and queues ONLY missing parts. Progress never resets!
  2. **Pause-Immune Dynamic Watchdogs:** Download watchdog threshold increased to **90s** to comfortably accommodate Telegram's 40–60s token refill window. Upload rolling average is only evaluated during active transmission (`stall_rounds == 0` and `span >= 50s`), preventing false triggers during ACK pauses.
  3. **Transport State Guarding:** Upload and download workers check `target_session.is_started.is_set()` before calling `.invoke()`, instantly rotating to another session if a socket is restarting.
  4. **Guaranteed Fresh JIT Token on Every Attempt:** `get_messages` is called immediately before each download attempt, eliminating `FILE_REFERENCE_EXPIRED` 100%.
  5. **FloodWait Watchdog Immunity (Commit `ae3a906`):**
     - **Verified Record:** 181.14 GB transferred in 5h 48m with **0 ERRORS** on Taiwan IP `35.185.154.61` (388 media files @ 8.9 MB/s).
     - **Discovery:** After 181 GB, Telegram issued a standard 816s FloodWait cooling pause. While `_execute_with_flood_retry` was sleeping, the stall watchdog saw no data for 50s and aborted the sleep!
     - **Safeguard:** Added `self._is_flood_waiting` flag. Watchdogs in both `migration.py` and `fast_uploader.py` now check this flag and pause their stall counters, allowing Telegram rate-limit penalties to elapse naturally and resume automatically without burning retry attempts.

## 12. Cross-DC `auth.ExportAuthorization` FloodWait Immunity & Airtight Failure Tracking
- **The Issue (Encountered on `#13385–#13402`):**
  1. Secondary download session `s2` invoked `auth.ExportAuthorization` consecutively after primary session `session` on foreign DC 4, triggering a 2581s FloodWait.
  2. `fast_uploader.py` blocked the download by sleeping 2581s for the *optional* secondary socket.
  3. The 90s stall watchdog saw 0 progress and killed the task, repeating 4 times and failing 5 files.
  4. Prefetch fallback in `_migrate_single_message` returned `None` without raising, causing the caller to immediately call `remove_failed_message`, wiping the failed IDs from `failed_messages.json` and hiding them from the live card (`❌ Errors: 5` with no brackets).
- **The Permanent Fix:**
  1. **Secondary Session FloodWait Bypass:** If `s2` hits FloodWait $>3\text{s}$, skip `s2` immediately, store `_DC_EXPORT_FLOOD_UNTIL[dc_id]`, and download at full speed with `session` alone.
  2. **Multi-Client Watchdog Immunity:** `_dl_stall_watchdog` checks `dl_client._is_flood_waiting` and never aborts during a legitimate rate-limit sleep.
  3. **Raise-on-Failure:** `_migrate_single_message` raises `RuntimeError` on empty download, ensuring failed messages are permanently tracked in `failed_messages.json` and never prematurely deleted.
  4. **In-Memory ID Fallback:** `_send_progress_update` and `_send_failed_messages_report` query `self.stats.failed_msg_ids` as fallback, guaranteeing failed IDs are always visible in real time.



