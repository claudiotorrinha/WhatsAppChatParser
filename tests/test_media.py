import unittest
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from wcp.manifest import ManifestLogger
from wcp import media
from wcp.util import media_artifact_stem


class TestMediaHelpers(unittest.TestCase):
    def test_tmp_path_preserves_extension(self):
        dst = Path("out/converted/PTT-1.wav")
        tmp = media._tmp_path_for(dst)
        self.assertTrue(tmp.name.endswith(".tmp.wav"))

        dst2 = Path("out/converted/PTT-2.mp3")
        tmp2 = media._tmp_path_for(dst2)
        self.assertTrue(tmp2.name.endswith(".tmp.mp3"))

    def test_ffmpeg_uses_tmp_with_extension(self):
        dst = Path("out/converted/PTT-3.wav")
        cmd = ["ffmpeg", "-y", "-i", "in.opus", str(dst)]
        with mock.patch("wcp.media.subprocess.run") as run_mock, \
             mock.patch("wcp.media.os.replace") as replace_mock:
            run_mock.return_value = mock.Mock(returncode=0, stderr="")
            media.ffmpeg_to_tmp_then_replace(cmd, dst)
            called_cmd = run_mock.call_args[0][0]
            self.assertTrue(str(called_cmd[-1]).endswith(".tmp.wav"))
            replace_mock.assert_called_once()

    def test_convert_to_mp3_passthrough_for_mp3_input(self):
        src = Path("input.mp3")
        dst = Path("out/converted/input.mp3")
        with mock.patch("wcp.media._copy_to_tmp_then_replace") as copy_mock, \
             mock.patch("wcp.media.ffmpeg_to_tmp_then_replace") as ffmpeg_mock:
            media.convert_to_mp3(src, dst)
            copy_mock.assert_called_once_with(src, dst)
            ffmpeg_mock.assert_not_called()

    def test_ensure_audio_reuses_wav_for_mp3_when_transcribing(self):
        class DummyTranscriber:
            def transcribe_wav(self, wav_path, language=None):
                return "ok"

        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "clip.opus").write_bytes(b"data")

            stats = {
                "missing_files": 0,
                "audio_mp3_created": 0,
                "audio_mp3_skipped": 0,
                "audio_mp3_failed": 0,
                "audio_wav_created": 0,
                "audio_wav_skipped": 0,
                "audio_wav_failed": 0,
                "audio_transcripts_created": 0,
                "audio_transcripts_skipped": 0,
                "audio_transcripts_failed": 0,
                "image_ocr_created": 0,
                "image_ocr_skipped": 0,
                "image_ocr_failed": 0,
                "image_ocr_filtered": 0,
                "image_ocr_deferred": 0,
                "audio_convert_seconds": 0.0,
                "audio_transcribe_seconds": 0.0,
                "audio_meta_seconds": 0.0,
                "image_ocr_seconds": 0.0,
                "image_meta_seconds": 0.0,
                "stage_parse_seconds": 0.0,
                "stage_media_preprocess_seconds": 0.0,
                "stage_output_write_seconds": 0.0,
                "stage_total_seconds": 0.0,
                "transcriber_init_seconds": 0.0,
            }

            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            mp = media.MediaProcessor(
                folder=folder,
                out_dir=out_dir,
                resume=True,
                manifest=manifest,
                stats=stats,
                convert_audio="mp3",
                transcriber=DummyTranscriber(),
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

            with mock.patch("wcp.media.convert_to_wav") as wav_mock, \
                 mock.patch("wcp.media.convert_to_mp3") as mp3_mock, \
                 mock.patch("wcp.media.atomic_write_text") as write_mock:
                wav_mock.side_effect = fake_convert_to_wav
                mp.ensure_audio("clip.opus")
                mp.finalize()

                self.assertEqual(wav_mock.call_count, 1)
                self.assertEqual(mp3_mock.call_count, 1)
                mp3_src = mp3_mock.call_args[0][0]
                self.assertEqual(Path(mp3_src).suffix.lower(), ".wav")
                write_mock.assert_called_once()

            manifest.close()

    def test_ensure_audio_skips_audio_meta_probe_when_hash_disabled(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "clip.opus").write_bytes(b"data")

            stats = {
                "missing_files": 0,
                "audio_mp3_created": 0,
                "audio_mp3_skipped": 0,
                "audio_mp3_failed": 0,
                "audio_wav_created": 0,
                "audio_wav_skipped": 0,
                "audio_wav_failed": 0,
                "audio_transcripts_created": 0,
                "audio_transcripts_skipped": 0,
                "audio_transcripts_failed": 0,
                "image_ocr_created": 0,
                "image_ocr_skipped": 0,
                "image_ocr_failed": 0,
                "image_ocr_filtered": 0,
                "image_ocr_deferred": 0,
                "audio_convert_seconds": 0.0,
                "audio_transcribe_seconds": 0.0,
                "audio_meta_seconds": 0.0,
                "image_ocr_seconds": 0.0,
                "image_meta_seconds": 0.0,
                "stage_parse_seconds": 0.0,
                "stage_media_preprocess_seconds": 0.0,
                "stage_output_write_seconds": 0.0,
                "stage_total_seconds": 0.0,
                "transcriber_init_seconds": 0.0,
            }

            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            mp = media.MediaProcessor(
                folder=folder,
                out_dir=out_dir,
                resume=True,
                manifest=manifest,
                stats=stats,
                convert_audio="none",
                transcriber=None,
                transcribe_lang=None,
                ocr_enabled=False,
                ocr_lang="por",
                ocr_mode="all",
                ocr_max=0,
                ocr_edge_threshold=18.0,
                ocr_downscale=512,
                hash_media=False,
            )

            with mock.patch("wcp.media.compute_media_meta") as meta_mock:
                mp.ensure_audio("clip.opus")
                meta_mock.assert_not_called()

            manifest.close()

    def test_ensure_audio_uses_collision_safe_artifact_names(self):
        class DummyTranscriber:
            def __init__(self):
                self.calls = 0

            def transcribe_wav(self, wav_path, language=None):
                self.calls += 1
                return f"tx-{self.calls}"

        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "clip.opus").write_bytes(b"data1")
            (folder / "clip.ogg").write_bytes(b"data2")

            stats = {
                "missing_files": 0,
                "audio_mp3_created": 0,
                "audio_mp3_skipped": 0,
                "audio_mp3_failed": 0,
                "audio_wav_created": 0,
                "audio_wav_skipped": 0,
                "audio_wav_failed": 0,
                "audio_transcripts_created": 0,
                "audio_transcripts_skipped": 0,
                "audio_transcripts_failed": 0,
                "image_ocr_created": 0,
                "image_ocr_skipped": 0,
                "image_ocr_failed": 0,
                "image_ocr_filtered": 0,
                "image_ocr_deferred": 0,
                "audio_convert_seconds": 0.0,
                "audio_transcribe_seconds": 0.0,
                "audio_meta_seconds": 0.0,
                "image_ocr_seconds": 0.0,
                "image_meta_seconds": 0.0,
                "stage_parse_seconds": 0.0,
                "stage_media_preprocess_seconds": 0.0,
                "stage_output_write_seconds": 0.0,
                "stage_total_seconds": 0.0,
                "transcriber_init_seconds": 0.0,
            }

            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            transcriber = DummyTranscriber()
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
                mp.ensure_audio("clip.opus")
                mp.ensure_audio("clip.ogg")
                mp.finalize()

            stem_opus = media_artifact_stem("clip.opus")
            stem_ogg = media_artifact_stem("clip.ogg")
            t1 = out_dir / "transcripts" / f"{stem_opus}.txt"
            t2 = out_dir / "transcripts" / f"{stem_ogg}.txt"
            self.assertTrue(t1.exists())
            self.assertTrue(t2.exists())
            self.assertEqual(stats["audio_transcripts_created"], 2)
            self.assertEqual(stats["audio_transcripts_skipped"], 0)
            self.assertEqual(transcriber.calls, 2)

            manifest.close()

    def test_transcription_status_reports_current_and_backlog(self):
        class BlockingTranscriber:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()

            def transcribe_wav(self, wav_path, language=None):
                self.started.set()
                self.release.wait(timeout=5)
                return "ok"

        with TemporaryDirectory() as td:
            td_path = Path(td)
            folder = td_path / "in"
            out_dir = td_path / "out"
            folder.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            (folder / "a.opus").write_bytes(b"a")
            (folder / "b.opus").write_bytes(b"b")

            stats = {
                "missing_files": 0,
                "audio_mp3_created": 0,
                "audio_mp3_skipped": 0,
                "audio_mp3_failed": 0,
                "audio_wav_created": 0,
                "audio_wav_skipped": 0,
                "audio_wav_failed": 0,
                "audio_transcripts_created": 0,
                "audio_transcripts_skipped": 0,
                "audio_transcripts_failed": 0,
                "image_ocr_created": 0,
                "image_ocr_skipped": 0,
                "image_ocr_failed": 0,
                "image_ocr_filtered": 0,
                "image_ocr_deferred": 0,
                "audio_convert_seconds": 0.0,
                "audio_transcribe_seconds": 0.0,
                "audio_meta_seconds": 0.0,
                "image_ocr_seconds": 0.0,
                "image_meta_seconds": 0.0,
                "stage_parse_seconds": 0.0,
                "stage_media_preprocess_seconds": 0.0,
                "stage_output_write_seconds": 0.0,
                "stage_total_seconds": 0.0,
                "transcriber_init_seconds": 0.0,
            }

            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)
            manifest.open()
            transcriber = BlockingTranscriber()
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
                mp.ensure_audio("b.opus")
                self.assertTrue(transcriber.started.wait(timeout=2))

                status = mp.transcription_status()
                self.assertTrue(status["enabled"])
                self.assertEqual(status["current"], "a.opus")
                self.assertGreaterEqual(status["pending"], 1)
                self.assertIsInstance(status["current_elapsed_seconds"], (int, float))

                transcriber.release.set()
                mp.finalize()

            status_after = mp.transcription_status()
            self.assertFalse(status_after["enabled"])
            manifest.close()


if __name__ == "__main__":
    unittest.main()
