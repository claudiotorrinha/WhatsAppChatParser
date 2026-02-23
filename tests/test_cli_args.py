import unittest

from wcp.main import _strip_legacy_args, build_arg_parser


class TestCliArgs(unittest.TestCase):
    def test_supported_model_is_accepted(self):
        ap = build_arg_parser()
        args = ap.parse_args(["x", "--whisper-model", "large-v3-turbo", "--force-cpu"])
        self.assertEqual(args.whisper_model, "large-v3-turbo")
        self.assertTrue(args.force_cpu)

    def test_speed_preset_is_accepted(self):
        ap = build_arg_parser()
        args = ap.parse_args(["x", "--speed-preset", "off"])
        self.assertEqual(args.speed_preset, "off")

    def test_legacy_flags_are_stripped(self):
        cleaned, ignored = _strip_legacy_args(["x", "--config", "cfg.json", "--tz=+01:00", "--no-report"])
        self.assertEqual(cleaned, ["x"])
        self.assertIn("--config", ignored)
        self.assertIn("--tz", ignored)
        self.assertIn("--no-report", ignored)

    def test_legacy_model_is_rejected(self):
        ap = build_arg_parser()
        with self.assertRaises(SystemExit):
            ap.parse_args(["x", "--whisper-model", "small"])


if __name__ == "__main__":
    unittest.main()
