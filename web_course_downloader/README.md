# 🎓 CourseVerse Web & Classplus Course Downloader Bot

A dedicated Telegram Bot engine designed to extract and stream courses, video lectures (m3u8/HLS/MP4), and PDF notes directly from websites and LMS platforms into Telegram channels.

---

## 🌟 Key Features

1. 🔐 **Classplus One-Click Scraper**:
   - Login via **Org Code + Mobile OTP** or **Direct Bearer Token**.
   - Browse full batch / store course trees (Folders, Chapters, Video Lectures, and PDF Notes).
2. ✨ **Zero Dynamic Watermarks**:
   - Directly downloads the raw backend HLS/m3u8 stream before web players inject moving watermarks.
3. ⚡ **Generic Multi-Platform Downloader**:
   - Supports any m3u8 playlist URL, direct MP4, PW, Vimeo, YouTube, and PDF notes.
4. 🎯 **Custom Branding & Channels**:
   - Automatically attaches your custom `thumb.jpg` thumbnail to all uploaded videos.
   - Allows setting any custom Destination Channel ID via `/start`.

---

## 🚀 How to Run

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `.env`
Create a `.env` file with:
```ini
API_ID=22152865
API_HASH=0628e97cf87f63fa942c75a4dc248db3
BOT_TOKEN=8845853554:AAH3k86jW0qN7c2Y-1s0a-Ksq_46UfW9n28
OWNER_ID=8383627571
```

### 3. Start Bot
```bash
python3 bot.py
```
Open Telegram, send `/start` to your bot, and follow the interactive buttons!
