"""
Unit tests for fast_uploader and watermark modules.
"""

import unittest
from pathlib import Path
from watermark import is_ffmpeg_available
from utils import parse_telegram_link, sanitize_caption


class TestFastUploaderAndWatermark(unittest.TestCase):

    def test_link_parsing_integrity(self):
        parsed = parse_telegram_link("https://t.me/c/1234567890/500")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.chat_id, -1001234567890)
        self.assertEqual(parsed.start_msg_id, 500)

    def test_caption_forward_stripping(self):
        raw = "Forwarded from Coaching Group\n\nChapter 5 Notes"
        cleaned = sanitize_caption(raw)
        self.assertEqual(cleaned, "Chapter 5 Notes")

    def test_ffmpeg_check_boolean(self):
        res = is_ffmpeg_available()
        self.assertIsInstance(res, bool)


if __name__ == "__main__":
    unittest.main()
