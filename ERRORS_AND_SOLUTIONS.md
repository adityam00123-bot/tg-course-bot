# Telegram Bot Errors & Permanent Solutions Knowledge Base
**Repository:** `tg-course-bot`
**Purpose:** Permanent technical memory of all encountered errors, root causes, and architectural solutions to ensure future AI agents or context resets never regress existing stability.

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

