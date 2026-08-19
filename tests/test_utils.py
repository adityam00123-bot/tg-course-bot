"""
Unit tests for utils.py functions:
- Telegram link parsing (private, public, range, direct IDs)
- Caption sanitization (forward header stripping, formatting preservation)
- Formatting helpers (progress bar, duration format)
"""

import unittest
from utils import (
    parse_telegram_link,
    parse_message_id_or_link,
    sanitize_caption,
    format_seconds,
    format_progress_bar,
    format_chat_display
)


class TestTelegramLinkParser(unittest.TestCase):

    def test_private_channel_link(self):
        link = "https://t.me/c/1234567890/42"
        res = parse_telegram_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res.chat_id, -1001234567890)
        self.assertEqual(res.start_msg_id, 42)
        self.assertIsNone(res.end_msg_id)
        self.assertTrue(res.is_private)

    def test_private_channel_range_link(self):
        link = "https://t.me/c/1234567890/10-25"
        res = parse_telegram_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res.chat_id, -1001234567890)
        self.assertEqual(res.start_msg_id, 10)
        self.assertEqual(res.end_msg_id, 25)

    def test_public_channel_link(self):
        link = "https://t.me/mathbatch2026/105"
        res = parse_telegram_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res.chat_id, "mathbatch2026")
        self.assertEqual(res.start_msg_id, 105)
        self.assertIsNone(res.end_msg_id)
        self.assertFalse(res.is_private)

    def test_public_channel_range_link(self):
        link = "https://telegram.me/mathbatch2026/50-100"
        res = parse_telegram_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res.chat_id, "mathbatch2026")
        self.assertEqual(res.start_msg_id, 50)
        self.assertEqual(res.end_msg_id, 100)

    def test_bare_link_without_scheme(self):
        link = "t.me/c/9876543210/99"
        res = parse_telegram_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res.chat_id, -1009876543210)
        self.assertEqual(res.start_msg_id, 99)

    def test_direct_integer_id(self):
        res = parse_telegram_link("55")
        self.assertIsNotNone(res)
        self.assertEqual(res.start_msg_id, 55)
        self.assertIsNone(res.end_msg_id)

    def test_invalid_input(self):
        self.assertIsNone(parse_telegram_link("not a link"))
        self.assertIsNone(parse_telegram_link(""))
        self.assertIsNone(parse_telegram_link("https://google.com/search"))


class TestCaptionSanitizer(unittest.TestCase):

    def test_strip_forwarded_from(self):
        raw = "Forwarded from Coaching Hub\n\nChapter 1 - Vectors Lecture Notes"
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, "Chapter 1 - Vectors Lecture Notes")

    def test_strip_bracketed_forward(self):
        raw = "[Forwarded from Admin Name]\nImportant formula sheet attached."
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, "Important formula sheet attached.")

    def test_strip_forwarded_message(self):
        raw = "Forwarded Message:\nPhysics problem set 4."
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, "Physics problem set 4.")

    def test_strip_fwd_prefix(self):
        raw = "Fwd: Solution video for Mock Test 2"
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, "Solution video for Mock Test 2")

    def test_preserve_clean_caption(self):
        raw = "📚 <b>Calculus Module 3</b>\n\nWatch this video before Sunday's live test."
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, raw)

    def test_empty_or_none(self):
        self.assertIsNone(sanitize_caption(None))
        self.assertIsNone(sanitize_caption(""))
        self.assertIsNone(sanitize_caption("   \n\n   "))
        self.assertIsNone(sanitize_caption("Forwarded from Test Channel"))


class TestFormatHelpers(unittest.TestCase):

    def test_format_seconds(self):
        self.assertEqual(format_seconds(45), "45s")
        self.assertEqual(format_seconds(90), "1m 30s")
        self.assertEqual(format_seconds(3665), "1h 1m 5s")

    def test_format_progress_bar(self):
        pbar_zero = format_progress_bar(0, 100)
        self.assertIn("0%", pbar_zero)
        pbar_half = format_progress_bar(50, 100)
        self.assertIn("50%", pbar_half)
        pbar_full = format_progress_bar(100, 100)
        self.assertIn("100%", pbar_full)

    def test_format_chat_display(self):
        self.assertEqual(format_chat_display(-100123, "Math Batch", "mathbatch"), "Math Batch (@mathbatch)")
        self.assertEqual(format_chat_display(-100123, "Math Batch", None), "Math Batch")
        self.assertEqual(format_chat_display(-100123, None, "mathbatch"), "@mathbatch")
        self.assertEqual(format_chat_display(-100123, None, None), "Chat [-100123]")


if __name__ == "__main__":
    unittest.main()
