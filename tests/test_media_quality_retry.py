import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from wcp import media
from wcp.manifest import ManifestLogger
from wcp.util import media_artifact_stem


class _RetryingDummyTranscriber:
    def __init__(self):
        self.calls: list[bool] = []

    def transcribe_wav(self, wav_path, language=None, quality_retry=False):
        self.calls.append(bool(quality_retry))
        if quality_retry:
            return (
                "Texto revisto com sucesso. A conversa segue com frases diferentes, "
                "sem repeticoes excessivas e sem ruido no final."
            )
        return ("Nao e? " * 220).strip()


class _RetryingTooShortDummyTranscriber:
    def __init__(self):
        self.calls: list[bool] = []

    def transcribe_wav(self, wav_path, language=None, quality_retry=False):
        self.calls.append(bool(quality_retry))
        if quality_retry:
            return "Fim com ruido \ufffd\ufffd\ufffd\ufffd\ufffd"
        return ("Nao e? " * 220).strip()


class TestMediaQualityRetry(unittest.TestCase):
    def test_quality_flagged_transcript_is_retried_and_replaced(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "a.opus").write_bytes(b"a")

            stats = {}
            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            transcriber = _RetryingDummyTranscriber()
            mp = media.MediaProcessor(
                folder=folder,
                out_dir=out_dir,
                resume=True,
                manifest=manifest,
                stats=stats,
                convert_audio="none",
                transcriber=transcriber,
                transcribe_lang=None,
                ocr_enabled=False,
                ocr_lang="por",
                ocr_mode="all",
                ocr_max=0,
                ocr_edge_threshold=18.0,
                ocr_downscale=512,
                hash_media=False,
            )

            def fake_convert_to_wav(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b"wav")

            with mock.patch("wcp.media.convert_to_wav", side_effect=fake_convert_to_wav):
                mp.ensure_audio("a.opus")
                mp.finalize()

            transcript_path = out_dir / "transcripts" / f"{media_artifact_stem('a.opus')}.txt"
            self.assertTrue(transcript_path.exists())
            content = transcript_path.read_text(encoding="utf-8")
            self.assertIn("Texto revisto com sucesso.", content)
            self.assertEqual(stats.get("audio_transcripts_created"), 1)
            self.assertGreaterEqual(stats.get("audio_transcript_quality_flagged", 0), 1)
            self.assertEqual(stats.get("audio_transcript_retry_attempted"), 1)
            self.assertEqual(stats.get("audio_transcript_retry_succeeded"), 1)
            self.assertEqual(transcriber.calls, [False, True])
            manifest.close()

    def test_retry_does_not_replace_with_too_short_still_bad_result(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "b.opus").write_bytes(b"b")

            stats = {}
            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            transcriber = _RetryingTooShortDummyTranscriber()
            mp = media.MediaProcessor(
                folder=folder,
                out_dir=out_dir,
                resume=True,
                manifest=manifest,
                stats=stats,
                convert_audio="none",
                transcriber=transcriber,
                transcribe_lang=None,
                ocr_enabled=False,
                ocr_lang="por",
                ocr_mode="all",
                ocr_max=0,
                ocr_edge_threshold=18.0,
                ocr_downscale=512,
                hash_media=False,
            )

            def fake_convert_to_wav(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b"wav")

            with mock.patch("wcp.media.convert_to_wav", side_effect=fake_convert_to_wav):
                mp.ensure_audio("b.opus")
                mp.finalize()

            transcript_path = out_dir / "transcripts" / f"{media_artifact_stem('b.opus')}.txt"
            self.assertTrue(transcript_path.exists())
            content = transcript_path.read_text(encoding="utf-8")
            self.assertIn("Nao e?", content)
            self.assertNotIn("Fim com ruido", content)
            self.assertEqual(stats.get("audio_transcript_retry_attempted"), 1)
            self.assertEqual(stats.get("audio_transcript_retry_succeeded", 0), 0)
            self.assertEqual(stats.get("audio_transcript_retry_failed"), 1)
            self.assertEqual(transcriber.calls, [False, True])
            manifest.close()


if __name__ == "__main__":
    unittest.main()
