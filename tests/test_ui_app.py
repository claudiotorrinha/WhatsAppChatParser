import unittest
from wcp import ui_app


class TestUiApp(unittest.TestCase):
    def test_resolve_out_dir(self):
        out = ui_app._resolve_out_dir("out")
        self.assertTrue(str(out).endswith("out"))

    def test_parse_helpers(self):
        self.assertTrue(ui_app._parse_bool("true"))
        self.assertFalse(ui_app._parse_bool("false"))
        self.assertEqual(ui_app._parse_int("5"), 5)
        self.assertIsNone(ui_app._parse_int("x"))
        self.assertEqual(ui_app._parse_float("2.5"), 2.5)
        self.assertIsNone(ui_app._parse_float("x"))

    def test_runtime_info_keys(self):
        info = ui_app._runtime_info()
        self.assertIn("cuda_available", info)
        self.assertIn("torch_version", info)
        self.assertIn("whisper_available", info)
        self.assertIn("openai_whisper_available", info)
        self.assertIn("faster_whisper_available", info)


if __name__ == "__main__":
    unittest.main()
