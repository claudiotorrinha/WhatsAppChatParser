import unittest

from wcp.main import build_arg_parser


class TestCliArgs(unittest.TestCase):
    def test_transcribe_backend_hf_is_accepted(self):
        ap = build_arg_parser({})
        args = ap.parse_args(["x", "--transcribe-backend", "hf", "--whisper-model", "large-v3-turbo"])
        self.assertEqual(args.transcribe_backend, "hf")
        self.assertEqual(args.whisper_model, "large-v3-turbo")


if __name__ == "__main__":
    unittest.main()

