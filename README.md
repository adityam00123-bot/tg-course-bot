# 🚀 Telegram Content Migration Bot (Pyrogram)

A production-ready Telegram content migration tool built in Python using **Pyrogram**. Designed specifically for educational coaching businesses, course creators, and batch managers to seamlessly migrate videos, documents, photos, audio, and text lessons across Telegram channels.

---

## ✨ Key Features

- **Dual-Client Architecture:**
  - **Userbot Client:** Authenticates with your personal Telegram account (phone + OTP/2FA), giving full read access to private/restricted channels and 2GB media upload capacity.
  - **Bot Client:** Authenticates via Bot Token from `@BotFather`, providing an interactive inline keyboard dashboard on Telegram.
- **Download-Then-Upload Pipeline (Restricted Content Support):**
  - Bypasses Telegram channel forward/copy restrictions (`noforwards` / protected content) by locally downloading media via `download_media()` and re-uploading clean new messages.
  - Automatically purges local temporary media immediately after each upload to keep disk usage minimal.
- **Interactive Inline Keyboard UI:**
  - `/start` displays live dashboard with source, destination, message range, and status.
  - Quick channel picker lists your top 10 pinned and recent channels as clickable buttons.
  - Conversational message range configuration: paste message links or enter numbers.
  - Live progress reports sent to your private chat every 25 messages.
- **Caption Sanitization:**
  - Automatically strips `"Forwarded from..."`, `"Forwarded Message:"`, and `"Fwd:"` headers while preserving your original lesson notes and markdown formatting.
- **Flood Control & Rate-Limiting:**
  - Random 1.0s – 3.0s delay between messages.
  - Automatic `FloodWait` detection and exponential sleep/retry logic.
- **Startup Peer Cache Synchronization:**
  - Pre-fetches dialogs on startup to eliminate `Peer id invalid` and resolution errors.
- **Process Lock Protection:**
  - Automatic PID lock file prevents duplicate polling instances from conflicting.
- **Dual Logging:**
  - Formatted terminal output + rotating file logging (`migration.log`).

---

## 📁 Project Structure

```
noble-meitner/
├── config.py             # Configuration validation, environment settings & dual logger setup
├── client.py             # Userbot & Bot client initialization, peer cache sync, PID lock manager
├── migration.py          # Core migration engine: download-upload pipeline, FloodWait handler
├── handlers.py           # Bot command handlers, inline callback state machine, UI menus
├── utils.py              # Telegram link parser, caption cleaner, duration & progress formatters
├── main.py               # Application entry point, lifecycle orchestrator, graceful shutdown
├── tests/
│   ├── __init__.py
│   └── test_utils.py     # Unit test suite for parsing, caption sanitizing, and formatting
├── requirements.txt      # Python package dependencies
├── .env.example          # Environment variables template
└── README.md             # Complete setup and deployment guide
```

---

## 📋 Prerequisites & Credentials

Before running the bot, you will need three items from Telegram:

### 1. Telegram API ID & API Hash
1. Go to **[https://my.telegram.org](https://my.telegram.org)** and log in with your phone number.
2. Click **API development tools**.
3. Create a new application (e.g. App title: `ContentMigrator`, Short name: `migrator`).
4. Copy your numerical **`api_id`** and 32-character **`api_hash`**.

### 2. Telegram Bot Token
1. Open Telegram and search for **`@BotFather`**.
2. Send `/newbot` and follow the prompts to name your bot and choose a username (e.g., `MyCourseMigratorBot`).
3. Copy the HTTP API token provided by BotFather (e.g. `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`).

### 3. Your Personal Telegram User ID
1. Search for **`@userinfobot`** or **`@raw_data_bot`** on Telegram.
2. Start the bot — it will reply with your numerical `Id` (e.g. `123456789`).
3. This is your **`OWNER_ID`** (ensures only you can control the migration bot).

---

## 🛠️ Step-by-Step Installation

### Step 1: Clone or Copy the Repository
```bash
git clone <repository_url>
cd noble-meitner
```

### Step 2: Create and Activate a Python Virtual Environment
```bash
# Linux / macOS / Google Cloud Shell
python3 -m venv venv
source venv/bin/activate

# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** `tgcrypto` installs pre-compiled C extensions for fast MTProto cryptographic operations. If running on a minimal Linux VM, ensure `gcc` and `python3-dev` are installed (`sudo apt update && sudo apt install -y build-essential python3-dev`).

---

## ⚙️ Configuration (.env)

Copy `.env.example` to create your active `.env` configuration file:

```bash
cp .env.example .env
```

Open `.env` in your favorite editor (e.g. `nano .env`) and fill in your details:

```env
# ===================================================
# Telegram API Credentials (from https://my.telegram.org)
# ===================================================
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef

# ===================================================
# Telegram Bot Token (from @BotFather)
# ===================================================
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# ===================================================
# Authorized Owner User ID (from @userinfobot)
# ===================================================
OWNER_ID=123456789

# ===================================================
# Optional: Phone number for initial userbot terminal login
# ===================================================
PHONE_NUMBER=+1234567890

# ===================================================
# Optional Settings
# ===================================================
DOWNLOAD_DIR=./downloads
LOG_FILE=migration.log
LOG_LEVEL=INFO
PROGRESS_INTERVAL=25
MIN_DELAY_SECONDS=1.0
MAX_DELAY_SECONDS=3.0
FLOOD_WAIT_MAX_SLEEP=300
```

---

## 🔑 First-Run Interactive Terminal Login

Run the main application for the first time:

```bash
python main.py
```

### What You Will See in the Terminal:

1. **Phone Number Prompt:**
   ```
   Connecting Userbot client...
   Enter phone number or bot token: +1234567890
   Is "+1234567890" correct? (y/N): y
   ```
2. **Telegram Login Code (OTP):**
   Telegram will send a login code to your official Telegram app (under the Telegram service notifications chat):
   ```
   Enter confirmation code: 48291
   ```
3. **Two-Factor Authentication (2FA) Password (if enabled):**
   If you have 2FA enabled on your Telegram account:
   ```
   Enter password (hidden): ********
   ```
4. **Successful Connection:**
   ```
   [18:30:00] [INFO   ] ✅ Userbot logged in successfully as: John Doe (@johndoe, ID: 123456789)
   [18:30:01] [INFO   ] 🔄 Performing initial dialogs sync to warm up peer cache and fetch channels...
   [18:30:03] [INFO   ] ✅ Dialogs sync completed. Found 12 total channels/groups. Cached top 10 for quick UI access.
   [18:30:04] [INFO   ] ✅ Bot client online as: @MyCourseMigratorBot (ID: 987654321)
   [18:30:04] [INFO   ] Sent online notification to owner ID: 123456789

   =================================================================
   🎉 Bot is running! Open Telegram and message @MyCourseMigratorBot to begin.
      Press CTRL+C in this terminal to stop.
   =================================================================
   ```

> 💡 **Session Persistence:** Once logged in, Pyrogram saves your authenticated session to `userbot.session` and `bot.session`. On all future runs, the bot will start **immediately without prompting for phone/OTP**!

---

## 📱 How to Use the Bot on Telegram

1. Open Telegram and send `/start` to your bot (e.g. `@MyCourseMigratorBot`).
2. You will see the **Content Migration Control Panel**:

```
📚 Content Migration Control Panel
━━━━━━━━━━━━━━━━━━━━━━━━━━

📥 Incoming Channel (Source):
   Not selected

📤 Outgoing Channel (Destination):
   Not selected

⚙️ Migration Mode:
   🔢 Range Mode: Not configured

📊 Job Status:
   ⚪ IDLE (Ready)

━━━━━━━━━━━━━━━━━━━━━━━━━━
[ 📥 Set Incoming ]  [ 📤 Set Outgoing ]
[ 🔢 Set Message Range ]  [ Switch to Full Mode 🔄 ]
[ 🚀 RUN MIGRATION ]  [ ⏹️ STOP ]
[ 🔄 Refresh Status ]  [ ❓ Help Guide ]
```

### Step 1: Set Incoming (Source) Channel
- Tap **📥 Set Incoming**.
- The bot displays your top 10 channels/batches (pinned channels first). Tap your source channel (e.g. `📌 1. Physics Batch 2025`).
- *Alternative:* Tap `✏️ Enter Custom Link / ID` and send a message link, username (`@physicsbatch`), or channel ID (`-1001234567890`).

### Step 2: Set Outgoing (Destination) Channel
- Tap **📤 Set Outgoing** and select your destination channel (e.g. `2. Physics Batch 2026`).

### Step 3: Choose Migration Mode
- **Option A (Specific Message Range):**
  1. Tap **🔢 Set Message Range**.
  2. The bot asks: *"Please send the link or ID of the START message"*.
  3. Paste the start link (e.g., `https://t.me/c/1234567890/10` or `10`).
  4. The bot asks: *"Now send the END message link or ID"*.
  5. Paste the end link (e.g., `https://t.me/c/1234567890/85` or `85`).
  6. *Shortcut:* You can also paste a range link directly in one message: `https://t.me/c/1234567890/10-85`.
- **Option B (Full Channel Migration):**
  - Tap **Switch to Full Mode 🔄**. The bot will scan and migrate all messages in the source channel in chronological order (oldest to newest).

### Step 4: Run Migration
- Tap **🚀 RUN MIGRATION**.
- The bot will begin downloading media locally, sanitizing captions, and uploading to the destination channel.
- Every 25 messages, you will receive a status notification showing:
  - Visual progress bar: `[██████░░░░] 60% (51/85)`
  - Number of videos/documents/photos uploaded
  - Number of text lessons sent
  - Elapsed time & current message ID
- Tap **⏹️ STOP** at any time to halt the migration safely.

---

## 🌐 Supported Link Formats

| Link Type | Example Format | Extracted Data |
| :--- | :--- | :--- |
| **Private Channel Single Message** | `https://t.me/c/1845920194/42` | Chat ID: `-1001845920194`, Msg ID: `42` |
| **Private Channel Range** | `https://t.me/c/1845920194/10-50` | Chat ID: `-1001845920194`, Range: `10` to `50` |
| **Public Channel Single Message** | `https://t.me/mybatch2026/100` | Username: `mybatch2026`, Msg ID: `100` |
| **Public Channel Range** | `https://t.me/mybatch2026/100-200` | Username: `mybatch2026`, Range: `100` to `200` |
| **Direct Message ID** | `42` | Message ID: `42` |

---

## ☁️ Cloud VM & VPS Deployment (Persistent 24/7)

### Method 1: Using `tmux` or `screen` (Recommended for Cloud Shell & Quick Runs)

1. Start a `tmux` session:
   ```bash
   tmux new -s migration
   ```
2. Activate your virtual environment and run the bot:
   ```bash
   source venv/bin/activate
   python main.py
   ```
3. Complete the first-run OTP login.
4. Detach from the session by pressing **`Ctrl+B`**, then **`D`**. The bot will continue running in the background.
5. To re-attach later:
   ```bash
   tmux attach -t migration
   ```

---

### Method 2: Running as a `systemd` Background Service (Recommended for Ubuntu/Debian VPS)

1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/telegram-migration.service
   ```
2. Paste the following configuration (replace `/path/to/noble-meitner` and `youruser` with your actual path and Linux user):
   ```ini
   [Unit]
   Description=Telegram Content Migration Bot
   After=network.target

   [Service]
   Type=simple
   User=youruser
   WorkingDirectory=/path/to/noble-meitner
   ExecStart=/path/to/noble-meitner/venv/bin/python main.py
   Restart=always
   RestartSec=10
   StandardOutput=append:/path/to/noble-meitner/migration.log
   StandardError=append:/path/to/noble-meitner/migration.log

   [Install]
   WantedBy=multi-user.target
   ```
3. Reload systemd, enable, and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-migration
   sudo systemctl start telegram-migration
   ```
4. View live logs:
   ```bash
   tail -f migration.log
   ```

---

## 🛡️ Robustness & Troubleshooting

### 1. `FloodWait` Errors
Telegram applies rate limits when messages are sent rapidly.
- The bot includes built-in `random.uniform(1.0, 3.0)` pauses between messages.
- If a `FloodWait` is encountered, the bot automatically pauses for the required duration (`e.value + 1` seconds) and retries the failed message without skipping or crashing.

### 2. `PeerIdInvalid` or Channel Not Found
- The bot automatically calls `get_dialogs()` on startup to cache MTProto access hashes for all your channels.
- If you create a new channel while the bot is running, simply restart the bot or select "Enter Custom Link/ID" and paste a message link from the new channel.

### 3. Disk Space Conservation
- The download-then-upload engine deletes every downloaded video/file in a `try...finally` block immediately after uploading.
- Even if an upload fails or the job is cancelled, temporary files are cleanly removed.

### 4. Duplicate Bot Instance Conflict
- If another process is already running, the PID lock manager prevents conflicting instances from polling simultaneously.
- If an unexpected crash leaves a lock file, delete `.bot.lock` manually before restarting.

---

## 🧪 Running Unit Tests

Run the test suite to verify link parsing, caption cleaning, and formatting utilities:

```bash
python -m unittest discover -s tests
```
