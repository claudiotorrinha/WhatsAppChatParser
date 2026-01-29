import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp import parser


class TestParserDetect(unittest.TestCase):
    def _write_chat(self, text: str) -> Path:
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "chat.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_detect_portuguese(self):
        chat = (
            "20/09/25, 9:41 da manhã - Joao: oi\n"
            "20/09/25, 9:42 da tarde - Joao: ola\n"
        )
        path = self._write_chat(chat)
        fmt = parser.detect_format(path)
        self.assertEqual(fmt.style, "pt")
        self.assertEqual(fmt.date_order, "dmy")

        total = parser.count_total_messages(path)
        self.assertEqual(total, 2)

        msgs = list(parser.iter_messages(path, tz_offset="+00:00"))
        self.assertEqual(len(msgs), 2)
        self.assertTrue(msgs[0].ts.startswith("2025-09-20T09:41:00+00:00"))

    def test_detect_android_mdy(self):
        chat = "9/20/2025, 9:41 AM - John: hi\n"
        path = self._write_chat(chat)
        fmt = parser.detect_format(path)
        self.assertEqual(fmt.style, "android")
        self.assertEqual(fmt.date_order, "mdy")

        msgs = list(parser.iter_messages(path, tz_offset="+00:00"))
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].ts.startswith("2025-09-20T09:41:00+00:00"))

    def test_detect_ios_dmy(self):
        chat = "[20/09/2025, 21:41] John: hi\n"
        path = self._write_chat(chat)
        fmt = parser.detect_format(path)
        self.assertEqual(fmt.style, "ios")
        self.assertEqual(fmt.date_order, "dmy")

        msgs = list(parser.iter_messages(path, tz_offset="+00:00"))
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].ts.startswith("2025-09-20T21:41:00+00:00"))

    def test_attachment_detection(self):
        chat = "9/20/2025, 9:41 AM - John: IMG-1.jpg (file attached)\n"
        path = self._write_chat(chat)
        msgs = list(parser.iter_messages(path, tz_offset="+00:00"))
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        self.assertEqual(msg.type, "image")
        self.assertEqual(len(msg.media), 1)
        self.assertEqual(msg.media[0].file, "IMG-1.jpg")
        self.assertIsNone(msg.text)

    def test_date_order_override(self):
        chat = "03/04/2025, 09:00 - John: hi\n"
        path = self._write_chat(chat)
        msgs = list(parser.iter_messages(path, tz_offset="+00:00", format_override="android", date_order_override="dmy"))
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].ts.startswith("2025-04-03T09:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
