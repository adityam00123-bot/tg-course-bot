"""
Pyrogram String Session Generator
Run this script once to generate your Pyrogram String Session string.
Copy the output and paste it into GitHub Repository Secrets as: SESSION_STRING
"""

import asyncio
from pyrogram import Client

async def main():
    print("=" * 65)
    print("      >> PYROGRAM STRING SESSION GENERATOR (GitHub Actions) <<      ")
    print("=" * 65)
    
    api_id_input = input("Enter API_ID (or press Enter to use default [32974135]): ").strip()
    api_id = int(api_id_input) if api_id_input else 32974135

    api_hash_input = input("Enter API_HASH (or press Enter to use default): ").strip()
    api_hash = api_hash_input if api_hash_input else "ca1558b1b02b76e2875cb03ed9f5311e"

    print("\n👉 Please enter your phone number with country code when prompted (e.g. +91XXXXXXXXXX):")
    
    async with Client(name="string_session_gen", api_id=api_id, api_hash=api_hash, in_memory=True) as client:
        session_str = await client.export_session_string()
        
        print("\n" + "=" * 65)
        print("✅ SUCCESS! HERE IS YOUR SESSION_STRING (Copy the entire text below):")
        print("=" * 65)
        print(f"\n{session_str}\n")
        print("=" * 65)
        print("🔒 KEEP THIS SECRET! Paste it into GitHub Secrets as 'SESSION_STRING'.")
        print("=" * 65 + "\n")

if __name__ == "__main__":
    asyncio.run(main())