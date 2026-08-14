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

    def test_retry_creates_retried_marker(self):
        """After a quality retry attempt, a .retried marker must exist."""
        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "c.opus").write_bytes(b"c")

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
                mp.ensure_audio("c.opus")
                mp.finalize()

            transcript_path = out_dir / "transcripts" / f"{media_artifact_stem('c.opus')}.txt"
            marker_path = transcript_path.with_suffix(".retried")
            self.assertTrue(transcript_path.exists())
            self.assertTrue(marker_path.exists(), ".retried marker should be created after retry")
            manifest.close()

    def test_already_retried_transcript_is_not_retried_again(self):
        """On a resumed run, a low-quality transcript with a .retried marker must NOT be retried."""
        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "d.opus").write_bytes(b"d")

            # Pre-create a low-quality transcript AND its .retried marker (simulating a prior run).
            transcripts_dir = out_dir / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            tfile = transcripts_dir / f"{media_artifact_stem('d.opus')}.txt"
            bad_text = ("Nao e? " * 220).strip()
            tfile.write_text(bad_text + "\n", encoding="utf-8")
            marker = tfile.with_suffix(".retried")
            marker.write_text("retried\n", encoding="utf-8")

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
                mp.ensure_audio("d.opus")
                mp.finalize()

            # Transcript content should be unchanged (the original bad text).
            content = tfile.read_text(encoding="utf-8")
            self.assertIn("Nao e?", content)
            # The transcriber should never have been called at all.
            self.assertEqual(transcriber.calls, [], "Transcriber should not be called for an already-retried transcript")
            self.assertEqual(stats.get("audio_transcript_retry_attempted", 0), 0)
            # Quality is still flagged (logged), but no retry was scheduled.
            self.assertGreaterEqual(stats.get("audio_transcript_quality_flagged", 0), 1)
            manifest.close()


if __name__ == "__main__":
    unittest.main()

