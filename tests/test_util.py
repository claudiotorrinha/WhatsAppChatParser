import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp import util


class TestUtil(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(util.clip("hello", 10), "hello")
        self.assertEqual(util.clip("hello", 0), "")
        self.assertEqual(util.clip("hello world", 6), "hello…")

    def test_fmt_eta(self):
        self.assertEqual(util.fmt_eta(5), "5s")
        self.assertEqual(util.fmt_eta(65), "1m05s")
        self.assertEqual(util.fmt_eta(3665), "1h01m")

    def test_atomic_write_text(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "out.txt"
            util.atomic_write_text(p, "ok")
            self.assertEqual(p.read_text(encoding="utf-8"), "ok")

    def test_media_artifact_stem_is_collision_safe(self):
        s1 = util.media_artifact_stem("clip.opus")
        s2 = util.media_artifact_stem("clip.ogg")
        self.assertNotEqual(s1, s2)

    def test_media_artifact_stem_normalizes_slashes(self):
        s1 = util.media_artifact_stem(r"Media\PTT-1.opus")
        s2 = util.media_artifact_stem("Media/PTT-1.opus")
        self.assertEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
