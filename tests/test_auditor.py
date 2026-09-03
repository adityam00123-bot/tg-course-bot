"""
Unit tests for auditor.py: Gap Scanner and Sequence Alignment Engine.
"""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from auditor import (
    record_message_map,
    load_message_map,
    update_message_map_batch,
    extract_source_id_from_dest_message,
    check_admin_permissions,
    MESSAGE_MAP_FILE
)
from pyrogram.enums import ChatMemberStatus


class TestAuditor(unittest.TestCase):

    def setUp(self):
        if MESSAGE_MAP_FILE.exists():
            try:
                MESSAGE_MAP_FILE.unlink()
            except Exception:
                pass

    def tearDown(self):
        if MESSAGE_MAP_FILE.exists():
            try:
                MESSAGE_MAP_FILE.unlink()
            except Exception:
                pass

    def test_message_map_persistence(self):
        src, dst = -100111, -100222
        record_message_map(src, dst, 101, 501)
        record_message_map(src, dst, 102, 502)

        m = load_message_map(src, dst)
        self.assertEqual(m.get(101), 501)
        self.assertEqual(m.get(102), 502)

        update_message_map_batch(src, dst, {102: 505, 103: 506})
        m2 = load_message_map(src, dst)
        self.assertEqual(m2.get(102), 505)
        self.assertEqual(m2.get(103), 506)

    def test_extract_source_id_from_file_name(self):
        msg = MagicMock()
        msg.empty = False
        msg.service = False
        msg.video = MagicMock(file_name="media_-1001895806383_2093.mp4")
        msg.document = None
        msg.caption = None
        msg.text = None

        sid = extract_source_id_from_dest_message(msg)
        self.assertEqual(sid, 2093)

    def test_extract_source_id_from_caption(self):
        msg = MagicMock()
        msg.empty = False
        msg.service = False
        msg.video = None
        msg.document = None
        msg.caption = "Physics Lecture [#2105] Full Concept"
        msg.text = None

        sid = extract_source_id_from_dest_message(msg)
        self.assertEqual(sid, 2105)

    def test_check_admin_permissions_owner(self):
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=999))
        member = MagicMock()
        member.status = ChatMemberStatus.OWNER
        client.get_chat_member = AsyncMock(return_value=member)

        import asyncio
        ok, msg = asyncio.run(check_admin_permissions(client, -100222))
        self.assertTrue(ok)
        self.assertEqual(msg, "OK")

    def test_check_admin_permissions_admin_without_delete(self):
        client = MagicMock()
        client.get_me = AsyncMock(return_value=MagicMock(id=999))
        member = MagicMock()
        member.status = ChatMemberStatus.ADMINISTRATOR
        member.privileges = MagicMock(can_delete_messages=False, can_post_messages=True)
        client.get_chat_member = AsyncMock(return_value=member)

        import asyncio
        ok, msg = asyncio.run(check_admin_permissions(client, -100222))
        self.assertFalse(ok)
        self.assertIn("Delete Messages", msg)


if __name__ == "__main__":
    unittest.main()
