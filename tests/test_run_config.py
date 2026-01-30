import unittest

from wcp.run_config import RunConfig


class TestRunConfig(unittest.TestCase):
    def test_validate_mutual_exclusion(self):
        cfg = RunConfig(folder="x", only_transcribe=True, only_ocr=True)
        errors = cfg.validate()
        self.assertTrue(any("mutually" in err for err in errors))

    def test_to_argv_defaults(self):
        cfg = RunConfig(folder="x")
        argv = cfg.to_argv()
        self.assertEqual(argv[0], "whatsapp_export_to_jsonl.py")
        self.assertEqual(argv[1], "x")
        self.assertNotIn("--no-resume", argv)

    def test_to_argv_custom(self):
        cfg = RunConfig(
            folder="x",
            tz="+01:00",
            out="custom",
            quiet=True,
            progress_every=10,
            format="pt",
            date_order="dmy",
            no_resume=True,
            no_manifest=True,
            no_report=True,
            no_md=True,
            md_max_chars=100,
            no_by_month=True,
            audio_workers=3,
            ocr_workers=4,
            hash_media=True,
            me=["ME"],
            them=["THEM"],
            convert_audio="wav",
            whisper_model="large-v3",
            lang="en",
            transcribe_backend="faster",
            no_ocr=True,
            ocr_lang="eng",
            ocr_mode="likely-text",
            ocr_max=5,
            ocr_edge_threshold=12.5,
            ocr_downscale=256,
            only_transcribe=True,
        )
        argv = cfg.to_argv(include_prog=False)
        for token in [
            "x",
            "--tz",
            "+01:00",
            "--out",
            "custom",
            "--quiet",
            "--progress-every",
            "10",
            "--format",
            "pt",
            "--date-order",
            "dmy",
            "--no-resume",
            "--no-manifest",
            "--no-report",
            "--no-md",
            "--md-max-chars",
            "100",
            "--no-by-month",
            "--audio-workers",
            "3",
            "--ocr-workers",
            "4",
            "--hash-media",
            "--me",
            "ME",
            "--them",
            "THEM",
            "--convert-audio",
            "wav",
            "--whisper-model",
            "large-v3",
            "--lang",
            "en",
            "--transcribe-backend",
            "faster",
            "--no-ocr",
            "--ocr-lang",
            "eng",
            "--ocr-mode",
            "likely-text",
            "--ocr-max",
            "5",
            "--ocr-edge-threshold",
            "12.5",
            "--ocr-downscale",
            "256",
            "--only-transcribe",
        ]:
            self.assertIn(token, argv)


if __name__ == "__main__":
    unittest.main()
