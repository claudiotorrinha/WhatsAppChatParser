import unittest

from wcp.run_config import RunConfig


class TestRunConfig(unittest.TestCase):
    def test_to_argv_defaults(self):
        cfg = RunConfig(folder="x")
        argv = cfg.to_argv()
        self.assertEqual(argv[0], "whatsapp_export_to_jsonl.py")
        self.assertEqual(argv[1], "x")
        self.assertNotIn("--whisper-model", argv)

    def test_to_argv_custom(self):
        cfg = RunConfig(
            folder="x",
            out="custom",
            quiet=True,
            force_cpu=True,
            no_transcribe=True,
            whisper_model="large-v3-turbo",
            speed_preset="off",
            no_ocr=True,
        )
        argv = cfg.to_argv(include_prog=False)
        for token in [
            "x",
            "--out",
            "custom",
            "--quiet",
            "--force-cpu",
            "--no-transcribe",
            "--whisper-model",
            "large-v3-turbo",
            "--speed-preset",
            "off",
            "--no-ocr",
        ]:
            self.assertIn(token, argv)

    def test_validate_rejects_unsupported_model(self):
        cfg = RunConfig(folder="x", whisper_model="small")
        errors = cfg.validate()
        self.assertTrue(any("whisper_model" in err for err in errors))

    def test_validate_rejects_unsupported_speed_preset(self):
        cfg = RunConfig(folder="x", speed_preset="fast")
        errors = cfg.validate()
        self.assertTrue(any("speed_preset" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
