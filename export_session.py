"""Helper script to export your permanent Pyrogram StringSession.
Once exported, put USERBOT_SESSION_STRING in your .env or Kaggle Cell 2,
and you will NEVER have to log in or enter an OTP code again!
"""

import os
import glob
import asyncio
from pathlib import Path
from pyrogram import Client
from config import Config


async def export():
    Config.reload()
    session_files = list(Path(Config.SESSION_DIR).glob("userbot*.session")) + list(Path("/kaggle/working").glob("userbot*.session"))
    target_name = "userbot"

    if session_files:
        # Pick the first non-empty userbot session
        for sf in session_files:
            if sf.stat().st_size > 0:
                target_name = sf.stem
                break
        print(f"🔍 Found active session file: {target_name}.session")
    else:
        print("❌ No active .session file found. Creating interactive session...")

    app = Client(
        target_name,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        workdir=str(Config.SESSION_DIR)
    )

    async with app:
        session_string = await app.export_session_string()
        print("\n" + "=" * 65)
        print("🎉 YOUR PERMANENT USERBOT SESSION STRING (SAVE THIS):")
        print("=" * 65)
        print(session_string)
        print("=" * 65)
        print("\n👉 Add this line to your Kaggle Cell 2:")
        print(f'USERBOT_SESSION_STRING={session_string}')
        print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(export())
