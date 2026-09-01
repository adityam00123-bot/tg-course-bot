# Telegram Bot Errors & Permanent Solutions Knowledge Base
**Repository:** `tg-course-bot`
**Purpose:** Permanent technical memory of all encountered errors, root causes, and architectural solutions to ensure future AI agents or context resets never regress existing stability.

---

## 0. Performance & Console Output

### Error: Slow Downloads & Ghost Restarts (Contention)
* **Symptom:** Downloads cap out at 3-5 MB/s even with 4 workers. Logs occasionally show downloads jumping back to 0% (e.g., 99.7% -> 2.0%).
* **Root Cause:** The `fast_download_media` cached `self.media_sessions[dc_id]` and shared a single MTProto `Session` across multiple concurrent downloads. MTProto sessions process `invoke()` requests sequentially on the TCP socket, causing bottleneck contention. Re-using the same session object also resets its bytes counter which creates the 0% ghost restarts.
* **Permanent Fix:** Changed `fast_download_media` to instantiate a **new, dedicated `Session`** for every download call, preventing contention and allowing full bandwidth utilization (10MB/s+).

### Error: Double-Download Restart & 15s Stall on Final Chunk (`LIMIT_INVALID`)
* **Symptom:** Every download reaches 99% or 100%, freezes for 10-15 seconds, and then restarts from 0% at slow 0.9 MB/s, downloading the entire file twice.
* **Root Cause:** MTProto's `upload.GetFile` strictly requires the `limit` parameter to be divisible by 4096 bytes (4 KB). Requesting `limit = part_size` (unaligned remaining bytes of the last chunk) caused Telegram to return `[400 LIMIT_INVALID]`. `_dl_worker` retried 10 times (15s freeze), failed, and fell back to `_orig_download_media`, which wiped the downloaded bytes and started downloading from 0% again at single-connection 0.9 MB/s.
* **Permanent Fix:**
  - In `fast_download_media`, always send `limit = chunk_size` (1048576, which is an exact multiple of 4096). Telegram returns `r.bytes` containing only the actual remaining bytes without error.
  - Removed silent fallback that wipes downloaded files.

### Error: Non-Downloadable Media / Text Messages in Pipeline (`ValueError: This message doesn't contain any downloadable media`)
* **Symptom:** Text messages, web previews, or service announcements (e.g. #952) fail 10 download attempts, wait 30s per retry (~3.5 minutes blocked), and trigger synchronous recovery.
* **Root Cause:** `_msg_needs_pipeline` was returning `True` for text messages that had web preview links or when watermark options were enabled, routing text messages to `_download_media_to_file`.
* **Permanent Fix:**
  - `_msg_needs_pipeline` explicitly verifies `bool(msg.video or msg.photo or msg.document or msg.audio or msg.voice or msg.animation or msg.video_note or msg.sticker)`.
  - Non-media text messages bypass the prefetch download queue completely and publish immediately in ~50ms via direct stream.
  - Added a fast exit guard in `_download_media_to_file` returning `None` immediately if no downloadable media is present.

### Error: 5–15s Upload Holds & FloodWait Penalties
* **Symptom:** During uploads, progress holds/freezes for 5–15 seconds at a time before resuming.
* **Root Cause:**
  1. Sending 512KB MTProto chunks in tight loops on a single TCP socket triggered Telegram's server-side burst flood throttle (`FLOOD_WAIT_X`), which Pyrogram was sleeping on silently.
  2. Transient TCP latency jitters triggered full `reset_client_sessions()`, causing 5–8s socket reconnections.
  3. Video thumbnails were uploaded sequentially after the main file, adding a 2-second tail pause at 100%.
* **Permanent Fix:**
  - Added 10ms micro-pacing yield in `fast_save_file` to eliminate `FLOOD_WAIT` penalties.
  - Restricted socket resets strictly to fatal transport errors (`BrokenPipe`, `ConnectionLost`), allowing transient timeouts to retry cleanly without socket destruction.
  - Parallelized video and thumbnail upload via `asyncio.gather()` in `_pipeline_prefetch`.

---

## 1. Telegram MTProto & Authentication Errors

### Error: `[400 API_ID_INVALID]`
* **Symptom:** `Telegram says: [400 API_ID_INVALID] - The api_id/api_hash combination is invalid (caused by "auth.ImportBotAuthorization")`.
* **Root Cause:** 
  1. `API_ID` (integer) and `API_HASH` (32-char hex string) passed to Pyrogram do not match an existing, active Telegram application on [my.telegram.org](https://my.telegram.org).
  2. Or `API_ID` evaluated to `0` at runtime due to `.env` not being reloaded with `override=True`.
* **Permanent Fix:**
  - `Config.reload()` is invoked dynamically in `Config.validate()` and `client.create_bot_client()` with `load_dotenv(override=True)`.
  - Credentials must come directly from `https://my.telegram.org/apps`.

### Error: `[400 ACCESS_TOKEN_INVALID]`
* **Symptom:** `Telegram says: [400 ACCESS_TOKEN_INVALID] - The bot access token is invalid`.
* **Root Cause:** The `BOT_TOKEN` string contains a typo or was revoked/regenerated in `@BotFather`.
* **Permanent Fix:** Obtain fresh token from Telegram's `@BotFather` -> `/mybots` -> Select Bot -> `API Token`.

---

## 2. Media Transfer & MTProto Socket Errors

### Issue: Single File Download Serialization (Downloads Not Concurrent)
* **Symptom:** In logs, 4 downloads are launched (`Downloading #1... Downloading #2...`), but only `#1` downloads while `#2, #3, #4` wait with 0 progress.
* **Root Cause:** A single Pyrogram `Client` instance maintains **only ONE TCP connection** to each Telegram Media DataCenter (DC). When multiple tasks call `client.download_media()` on the same instance, MTProto serializes requests over that single connection.
* **Permanent Fix:** 
  - Created dedicated **`downloader_pool`** (`asyncio.Queue` of independent `Client` instances created with `session_string` and `in_memory=True`).
  - Each download task leases an isolated `Client` with its own dedicated TCP socket, allowing true simultaneous downloads.

### Error: `[Errno 32] Broken pipe` / `ConnectionResetError` / `ConnectionLost`
* **Symptom:** Upload or download suddenly raises `Broken pipe` or `ConnectionResetError` when transferring large files.
* **Root Cause:** Telegram's DC closes idle media sockets or abruptly resets TCP sessions during high-volume transfers.
* **Permanent Fix:**
  - Implemented `reset_client_sessions(client)` in `fast_uploader.py` and `migration.py`.
  - Safely stops broken DC sessions in `client.media_sessions` and triggers `session.restart()` so the next chunk retries on a fresh, healthy socket.

### Error: `[400 FILE_REFERENCE_EXPIRED]`
* **Symptom:** `Telegram says: [400 FILE_REFERENCE_EXPIRED] - The file reference has expired (caused by "upload.GetFile")`.
* **Root Cause:** Telegram's internal `file_reference` token inside a `Message` object expires after a few hours or during long migration queues.
* **Permanent Fix:** In `_download_media_to_file`, on retry or expired error detection, the bot calls `dl_client.get_messages(chat_id, message_ids=[msg.id])` to fetch a fresh `Message` object with a brand new `file_reference` from Telegram.

### Error: `[400 FILE_PART_X_MISSING]`
* **Symptom:** When publishing a pre-uploaded `InputFileBig` to the destination channel, Telegram returns `[400 FILE_PART_X_MISSING]`.
* **Root Cause:** If an MTProto chunk dropped silently or timed out before Telegram Cloud acknowledged all parts.
* **Permanent Fix:**
  - In `fast_uploader.py`, `fast_save_file` enforces strict acknowledgement and retry for every 512KB chunk.
  - In `migration.py`, `_pipeline_upload_slot` wraps `_send_pre_uploaded_media` in a try/except block. If `FILE_PART_X_MISSING` occurs, it falls back instantly to direct local file upload (`self.client.send_video/send_document`), guaranteeing zero message drops.

---

## 3. Concurrency & Event Loop Errors

### Error: `RuntimeError: read() called while another coroutine is already waiting for incoming data`
* **Symptom:** Python 3.12 `asyncio.StreamReader` collision when Pyrogram's `Session.stop()` or `Session.restart()` is called concurrently.
* **Root Cause:** Python 3.12 enforces strict single-waiter protection on `StreamReader.read()`. If two coroutines attempt to close/restart the session simultaneously, Python throws a collision exception.
* **Permanent Fix:**
  - In `fast_uploader.py`, monkey-patched `Session.stop = _safe_session_stop` and `Session.restart = _safe_session_restart` protected by `_session_restart_lock`.
  - The reader task is cleanly cancelled and awaited before closing the underlying transport.

### Error: `TypeError: '>' not supported between instances of 'NoneType' and 'int'`
* **Symptom:** `[WARNING] [Pipeline] Prefetch error for #XXX: '>' not supported between instances of 'NoneType' and 'int'`.
* **Root Cause:** When a message has no metadata file size (or is photo/custom document), `expected_size` is `None`. Comparing `expected_size > 0` directly threw a TypeError.
* **Permanent Fix:** In `migration.py` line 1238, guarded with `if expected_size is not None and expected_size > 0:`.

---

## 4. Hardware & Scaling Configuration Rules

| Environment | Optimal DL Streams | Optimal UL Streams | Optimal FFmpeg | Total Connections |
| :--- | :---: | :---: | :---: | :---: |
| **Kaggle (4 vCPU, 30GB RAM)** | **2 Parallel Streams** | **2 Parallel Streams** | **2-4 Workers** | **4 MTProto Sockets** |
| **Colab / Cloud Shell (2 vCPU)** | **2 Parallel Streams** | **2 Parallel Streams** | **2 Workers** | **4 MTProto Sockets** |

> **Rule:** Never exceed 4-6 simultaneous MTProto connections per Telegram account. Exceeding 6 triggers DC-level throttling (<500 KB/s) or FloodWaits.

---

## 5. Live In-Place Progress Dashboard Architecture
* **Problem:** Spamming 100+ raw logger lines every 5 seconds floods Kaggle/Cloud shell scrollback and obscures real-time status.
* **Architecture:**
  - `_active_transfers`: Central dictionary in `MigrationEngine` tracking all active download & upload slots.
  - `_live_progress_ticker_loop`: Unbuffered 1.0s ticker printing real-time MB transferred, speed in MB/s, and progress percentages side-by-side using `\r\033[K`.
  - Clean Completion Milestones: Only outputs permanent clean log lines upon milestone events (`✅ [Downloaded #898] (340.9 MB) in 42s (8.1 MB/s)`).

