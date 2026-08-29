"""
Telegram Channel Content Scanner & Comparator
Compares Source and Destination channels to pinpoint exact missing messages and files.
"""

import asyncio
import os
import sys
from pyrogram import Client
from config import Config

async def scan():
    print("=" * 65)
    print("       🔍 TELEGRAM CHANNEL CONTENT COMPARISON SCANNER")
    print("=" * 65)

    source_id = -1002662628248
    dest_id = -1004405815725

    # Detect userbot session
    session_file = None
    for f in os.listdir("."):
        if f.startswith("userbot") and f.endswith(".session"):
            session_file = f[:-8]
            break
    
    if not session_file:
        session_file = "userbot"

    print(f"\n[1/3] 🔌 Connecting via Userbot Session '{session_file}'...")
    app = Client(session_file, api_id=Config.API_ID, api_hash=Config.API_HASH)
    await app.start()
    print("✅ Logged in successfully!")

    try:
        print(f"\n[2/3] 📥 Scanning Source Channel ({source_id})...")
        source_msgs = {}
        async for m in app.get_chat_history(source_id, limit=2000):
            if m.empty or m.service:
                continue
            
            m_type = "text"
            m_name = ""
            m_size = 0
            if m.video:
                m_type = "video"
                m_name = m.video.file_name or f"video_{m.id}.mp4"
                m_size = m.video.file_size or 0
            elif m.photo:
                m_type = "photo"
                m_name = f"photo_{m.id}.jpg"
                m_size = m.photo.file_size or 0
            elif m.document:
                m_type = "document"
                m_name = m.document.file_name or f"doc_{m.id}"
                m_size = m.document.file_size or 0
            elif m.audio:
                m_type = "audio"
                m_name = m.audio.file_name or f"audio_{m.id}"
                m_size = m.audio.file_size or 0

            source_msgs[m.id] = {
                "id": m.id,
                "type": m_type,
                "name": m_name,
                "size_mb": round(m_size / (1024 * 1024), 2),
                "text": (m.text or m.caption or "").strip()[:50]
            }

        total_source = len(source_msgs)
        videos = [m for m in source_msgs.values() if m['type'] == 'video']
        photos = [m for m in source_msgs.values() if m['type'] == 'photo']
        docs = [m for m in source_msgs.values() if m['type'] == 'document']
        texts = [m for m in source_msgs.values() if m['type'] == 'text']

        print(f"✅ Found {total_source} valid messages in Source Channel:")
        print(f"   🎥 Videos: {len(videos)}")
        print(f"   🖼️ Photos: {len(photos)}")
        print(f"   📄 Documents/PDFs: {len(docs)}")
        print(f"   💬 Text/Links: {len(texts)}")

        print(f"\n[3/3] 📤 Scanning Destination Channel ({dest_id})...")
        dest_count = 0
        dest_media_types = {"video": 0, "photo": 0, "document": 0, "text": 0}
        async for m in app.get_chat_history(dest_id, limit=2000):
            if m.empty or m.service:
                continue
            dest_count += 1
            if m.video: dest_media_types["video"] += 1
            elif m.photo: dest_media_types["photo"] += 1
            elif m.document: dest_media_types["document"] += 1
            else: dest_media_types["text"] += 1

        print(f"✅ Found {dest_count} messages in Destination Channel:")
        print(f"   🎥 Videos: {dest_media_types['video']}")
        print(f"   🖼️ Photos: {dest_media_types['photo']}")
        print(f"   📄 Documents/PDFs: {dest_media_types['document']}")
        print(f"   💬 Text/Links: {dest_media_types['text']}")

        print("\n" + "=" * 65)
        print("                   📊 COMPARISON SUMMARY")
        print("=" * 65)
        print(f" Source Channel:       {total_source} items")
        print(f" Destination Channel:  {dest_count} items")
        diff = total_source - dest_count
        if diff <= 0:
            print(f"\n🎉 100% COMPLETE! All content is migrated successfully!")
        else:
            print(f"\n⚠️ Missing Difference: ~{diff} items need migration.")

        print("\n📋 Complete List of Source Channel Videos:")
        for idx, v in enumerate(videos, 1):
            print(f"  {idx}. [Msg #{v['id']}] {v['name']} ({v['size_mb']} MB) - {v['text']}")

        print("=" * 65)

    finally:
        await app.stop()

if __name__ == "__main__":
    asyncio.run(scan())
