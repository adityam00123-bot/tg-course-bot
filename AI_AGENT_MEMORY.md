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

## 13. Upload Speed Regression Resolution & Zero-Crawl Guard (September 2026)
- **The Problem:** On the PTC channel (#686–#698), 1.5 GB uploads degraded to 0.2–0.8 MB/s and took >11 minutes.
- **Root Causes Discovered & Fixed:**
  1. **Watchdog Neutering:** Commit `01b46c3` lowered the upload watchdog abort threshold to 0.02 MB/s (20 KB/s) and added `curr < file_size * 0.10`. This disabled the watchdog for 90%+ of an upload, allowing sockets to crawl indefinitely at 0.2 MB/s. Reverted to Golden Commit `2e2cbc5` logic: 45s window, evaluated at `span >= 35.0s` when `stall_rounds == 0`, aborting cleanly at `< 1.0 MB/s`.
  2. **Exponential Backoff Removal:** Chunk retry sleeps were exponentially backing off up to 3.0s, leaving workers idle. Reverted to fixed **0.1s (100ms)** delay.
  3. **Synchronous Upload Socket Healing:** Workers await deduplicated `safe_restart_session(target_session)` on transport errors instead of fire-and-forget `create_task`, eliminating `NoneType` and `closed=True` collision storms.
  4. **Reference:** Complete analysis in `UPLOAD_REGRESSION_RESEARCH.md`.
  5. **Rollback Baseline for Zero-Error Runs:** Commit `7ad5d51` (proven zero-error 345+ GB baseline).

## 14. Telegram Ingress Burst Rate Limits & Optimum Sizing (September 2026)
- **The Experiment:** We tested 5 sockets + 16 workers and an 8.0s jitter threshold (Commit `f0d7366`) aiming to saturate high-latency routes from Europe to DC5 (Singapore).
- **Empirical Findings (Taiwan Run, Message #2659):**
  1. On low-latency routes (~30ms RTT between Taiwan and Singapore DC 5), 16 workers across 5 sockets burst throughput up to **55.3 MB/s**.
  2. This instantaneous burst exceeds Telegram's MTProto edge token bucket refill rate and receiver window buffer.
  3. Consequently, Telegram temporarily stops sending TCP ACKs / throttles packets, creating a periodic 2-4 second stall (saw-tooth speed collapse to 0.6–1.5 MB/s at ~380MB, ~740MB, and ~1100MB marks).
  4. For file #2659 (1240.8 MB), these repeated stalls dragged the average upload speed down to 11.6 MB/s (1m 46s).
- **The Permanent Golden Configuration (Reverted & Confirmed):**
  1. **Socket Pool:** Fixed to **4 parallel media sockets** for files >30 parts (`fast_uploader.py`).
  2. **Worker Concurrency:** Fixed to **12 workers** on 4-vCPU systems (`max(10, min(cpu*3, 16))` in `config.py`).
  3. **Slow-Chunk Threshold:** Restored to **4.0s** (`chunk_dur > 4.0`).
## 15. Elimination of Premature Socket Restarts & Cascading Teardowns (September 2026)
- **The Incident (Encountered on #2951, 2.0 GB File):**
  1. After 5h 42m of flawless migration (220.91 GB transferred, 527 media files, 0 errors), file #2951 stalled repeatedly across 10 upload attempts.
  2. At ~340 MB, Telegram's edge buffer filled up during normal MTProto token refill, causing a healthy chunk to take 4.1s.
  3. Legacy logic `chunk_dur > 4.0` spawned a background `safe_restart_session(target_session)` on a *successful* chunk.
  4. Tearing down the active TCP connection mid-flight caused other concurrent workers sharing that socket to crash simultaneously with `[Errno 32] Broken pipe` and `handler is closed`.
  5. These errors triggered additional `safe_restart_session` calls, plunging all 4 sockets into a restart storm, collapsing speeds to 0.1 MB/s, and tripping the 45s watchdog.
- **The Permanent Fix:**
  1. **Never Restart on Success:** Removed `chunk_dur > 4.0` socket restart completely. Sockets are NEVER closed when chunks succeed.
  2. **Timeout Immunity:** Transient `asyncio.TimeoutError` or `"timed out"` no longer tears down sockets. Workers rotate to the next socket (`session_idx += 1`) and retry cleanly.
  3. **Guarded Transport Restarts:** Sockets are only restarted if `not is_session_alive(target_session)` on fatal transport disconnects (`broken pipe`, `connectionreset`, `handler is closed`, `closed=true`).

## 16. Watchdog Sizing & 1.1 MB/s Zombie Crawl Lesson (Commit `7ad5d51` Rollback)
- **The Incident (Observed on #6603 & #6626):**
  1. An earlier attempt to prevent ~50 MB early aborts widened the watchdog window to 60s at `< 0.5 MB/s` and added `curr < file_size * 0.50`, while simultaneously increasing chunk timeouts to 20s/25s and stripping timeout recovery from sockets.
  2. When a socket experienced silent latency or Telegram DC packet throttling, workers queued on that socket and hung for 20–25s. Because timeouts were not treated as dead transport, the socket was never healed.
  3. Throughput collapsed to 1.1 MB/s. Because 1.1 MB/s > 0.5 MB/s, and transfers past 50% were protected from aborts, **the watchdog never aborted**! File #6603 was trapped in a zombie crawl for **16 minutes 51 seconds**, and #6626 crawled at 1.1 MB/s indefinitely.
- **The Permanent Baseline:**
  1. Reverted `fast_uploader.py` to Golden Baseline commit `7ad5d51` (which transferred 345+ GB with 0 errors).
  2. **Golden Watchdog:** 45s sliding window (`span >= 35.0s`, `len(history) >= 7`), aborting cleanly at `< 1.0 MB/s` with NO 50% cutoff guard. Sockets are NEVER permitted to crawl at 1.1 MB/s for 16 minutes; they abort within 35s, refresh fresh sessions, and complete in <60 seconds at 25–35 MB/s.
  3. **RPC Timeout:** `timeout=12` on `invoke()`, wrapped in `timeout=20.0`.
  4. **Socket Self-Healing:** Chunks taking >4.0s or hitting timeouts immediately heal the session via `safe_restart_session(target_session)`.

## 17. Kaggle Terminal Output Cleanness & Smoothed Speed Display
- **The Clean Terminal & EMA Metric:**
  1. **Clean In-Place Ticker:** Pure in-place `\r` with dynamic space padding, and `_clear_progress_line()` wipes the line with 160 spaces before milestone logs (`[Downloaded #X]`, `[Uploaded #X]`). No periodic heartbeat loggers to prevent mashed lines.
  2. **Smoothed EMA Speed Metric:** EMA weights set to `0.3 * inst + 0.7 * prev` in `migration.py` so momentary 1-second MTProto ACK pauses do not cause visual speed collapses.
  3. **Sub-Second Formatting:** Tiny files (<500 KB) format cleanly as `instant` or `KB/s`.






