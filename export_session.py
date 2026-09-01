"""Helper script to export your permanent Pyrogram StringSession.
Once exported, put USERBOT_SESSION_STRING in your .env or Kaggle Cell 2,
and you will NEVER have to log in or enter an OTP code again!
"""

import asyncio
from pathlib import Path
from pyrogram import Client
from config import Config


async def export():
    sess_path = Config.SESSION_DIR / "userbot.session"
    # Also check userbot_OWNER_ID.session
    owner_sess = Config.SESSION_DIR / f"userbot_{Config.OWNER_ID}.session"
    target_name = "userbot"
    if not sess_path.exists():
        if owner_sess.exists():
            target_name = f"userbot_{Config.OWNER_ID}"
        else:
            print("❌ No active .session file found. Logging in interactively now...")
            target_name = "userbot"

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
        print("\n👉 Add this line to your .env or Kaggle Cell 2:")
        print(f'USERBOT_SESSION_STRING={session_string}')
        print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(export())
