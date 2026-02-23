import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp.manifest import ManifestLogger
from wcp.models import MediaRef, Message
from wcp.output import write_outputs
from wcp.util import media_artifact_stem


class TestOutputMediaLookup(unittest.TestCase):
    def test_uses_collision_safe_artifact_paths(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            out_dir = td_path / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = media_artifact_stem("clip.opus")
            (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
            (out_dir / "converted").mkdir(parents=True, exist_ok=True)
            (out_dir / "transcripts" / f"{stem}.txt").write_text("hello\n", encoding="utf-8")
            (out_dir / "converted" / f"{stem}.mp3").write_bytes(b"mp3")

            msg = Message(
                ts="2025-01-01T10:00:00+00:00",
                sender="A",
                type="audio",
                text=None,
                media=[MediaRef(file="clip.opus", kind="audio")],
                source_line="line-1",
            )
            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)

            write_outputs(
                messages=[msg],
                folder=td_path,
                out_dir=out_dir,
                md_max_chars=4000,
                write_md=False,
                write_by_month=False,
                me=[],
                them=[],
                manifest=manifest,
            )

            line = (out_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()[0]
            obj = json.loads(line)
            self.assertEqual(obj["media"][0]["converted_file"], f"converted/{stem}.mp3")
            self.assertEqual(obj["enrichment"]["transcript_file"], f"transcripts/{stem}.txt")
            self.assertEqual(obj["enrichment"]["transcript_text"], "hello")

    def test_falls_back_to_legacy_stem_artifacts(self):
        with TemporaryDirectory() as td:
            td_path = Path(td)
            out_dir = td_path / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
            (out_dir / "converted").mkdir(parents=True, exist_ok=True)
            (out_dir / "transcripts" / "clip.txt").write_text("legacy\n", encoding="utf-8")
            (out_dir / "converted" / "clip.wav").write_bytes(b"wav")

            msg = Message(
                ts="2025-01-01T10:00:00+00:00",
                sender="A",
                type="audio",
                text=None,
                media=[MediaRef(file="clip.opus", kind="audio")],
                source_line="line-1",
            )
            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)

            write_outputs(
                messages=[msg],
                folder=td_path,
                out_dir=out_dir,
                md_max_chars=4000,
                write_md=False,
                write_by_month=False,
                me=[],
                them=[],
                manifest=manifest,
            )

            line = (out_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()[0]
            obj = json.loads(line)
            self.assertEqual(obj["media"][0]["converted_file"], "converted/clip.wav")
            self.assertEqual(obj["enrichment"]["transcript_file"], "transcripts/clip.txt")
            self.assertEqual(obj["enrichment"]["transcript_text"], "legacy")


if __name__ == "__main__":
    unittest.main()
