"""
Unit tests for migration engine server copy & fallback logic.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
from migration import MigrationEngine, MigrationConfig


class TestMigrationServerCopy(unittest.TestCase):

    def setUp(self):
        self.userbot = MagicMock()
        self.userbot.send_video = AsyncMock()
        self.userbot.send_document = AsyncMock()
        self.userbot.send_message = AsyncMock()
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.engine = MigrationEngine(self.userbot, self.bot, 123456789)
        self.engine.config.dest_chat_id = -1009999999999

    def test_instant_server_copy_video_success(self):
        msg = MagicMock()
        msg.id = 101
        msg.video = MagicMock(file_id="BAACAgIAAxkBAAI...", duration=120, width=1280, height=720, file_size=50000000)
        msg.document = None
        msg.photo = None
        msg.audio = None
        msg.voice = None
        msg.video_note = None

        import asyncio
        res = asyncio.run(self.engine._try_instant_server_copy(msg, -1009999999999))
        self.assertTrue(res)
        self.assertEqual(self.engine.stats.media_count, 1)
        self.assertEqual(self.engine.stats.total_bytes_migrated, 50000000)
        self.userbot.send_video.assert_awaited_once()

    def test_instant_server_copy_exception_returns_false_for_fallback(self):
        self.userbot.send_video.side_effect = Exception("CHAT_FORWARDS_RESTRICTED")
        msg = MagicMock()
        msg.id = 102
        msg.caption = "Clean Caption"
        msg.caption_entities = None
        msg.video = MagicMock(file_id="BAACAgIAAxkBAAI...", duration=120, width=1280, height=720, file_size=50000000)
        msg.document = None
        msg.photo = None
        msg.audio = None
        msg.voice = None
        msg.video_note = None

        import asyncio
        res = asyncio.run(self.engine._try_instant_server_copy(msg, -1009999999999))
        self.assertFalse(res)

    def test_checkpoint_persistence_and_reset(self):
        from migration import save_checkpoint, load_checkpoint, reset_checkpoint
        src = -1001111111111
        dst = -1002222222222

        # Save checkpoint
        save_checkpoint(src, dst, 42)
        self.assertEqual(load_checkpoint(src, dst), 42)

        # Update checkpoint
        save_checkpoint(src, dst, 85)
        self.assertEqual(load_checkpoint(src, dst), 85)

        # Reset checkpoint
        reset_checkpoint(src, dst)
        self.assertEqual(load_checkpoint(src, dst), 0)


if __name__ == "__main__":
    unittest.main()
