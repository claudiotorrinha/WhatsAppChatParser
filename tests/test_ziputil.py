import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from wcp import ziputil


class TestZipUtil(unittest.TestCase):
    def test_safe_extract_blocks_zip_slip(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            zip_path = td_path / "bad.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../evil.txt", "nope")
            with self.assertRaises(RuntimeError):
                ziputil.safe_extract_zip(zip_path, td_path / "out")

    def test_find_export_root(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            sub = td_path / "nested"
            sub.mkdir(parents=True, exist_ok=True)
            chat = sub / "WhatsApp Chat.txt"
            chat.write_text("test", encoding="utf-8")
            root = ziputil.find_export_root(td_path)
            self.assertEqual(root.resolve(), sub.resolve())


if __name__ == "__main__":
    unittest.main()
