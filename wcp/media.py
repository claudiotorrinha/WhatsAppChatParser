from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .manifest import ManifestLogger
from .models import MediaMeta
from .ocr_gate import should_ocr_image
from .transcribe import Transcriber
from .util import (atomic_write_text, ffprobe_duration_seconds, image_dimensions,
                   relpath_posix, sha256_file)


def ffmpeg_to_tmp_then_replace(cmd: list[str], dst: Path) -> None:
    # Ensure atomic output: write to dst.tmp then rename.
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    cmd = cmd[:-1] + [str(tmp)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.replace(tmp, dst)


def convert_to_wav(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ac", "1",
        "-ar", "16000",
        str(dst),
    ]
    ffmpeg_to_tmp_then_replace(cmd, dst)


def convert_to_mp3(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-c:a", "libmp3lame",
        "-q:a", "3",
        str(dst),
    ]
    ffmpeg_to_tmp_then_replace(cmd, dst)


def compute_media_meta(path: Path, kind: str, do_hash: bool = False) -> MediaMeta:
    meta = MediaMeta()
    try:
        meta.size_bytes = path.stat().st_size
    except Exception:
        meta.size_bytes = None

    if do_hash:
        try:
            meta.sha256 = sha256_file(path)
        except Exception:
            meta.sha256 = None

    if kind == "audio":
        meta.duration_seconds = ffprobe_duration_seconds(path)

    if kind == "image":
        w, h = image_dimensions(path)
        meta.width, meta.height = w, h

    return meta


def ocr_image(img_path: Path, lang: str = "por") -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception as e:
        raise RuntimeError("OCR requires pytesseract and pillow") from e

    with Image.open(img_path) as img:
        text = pytesseract.image_to_string(img, lang=lang)
    return text.strip()


class MediaProcessor:
    def __init__(
        self,
        folder: Path,
        out_dir: Path,
        resume: bool,
        manifest: ManifestLogger,
        stats: dict,
        *,
        convert_audio: str,
        transcriber: Optional[Transcriber],
        transcribe_lang: str,
        ocr_enabled: bool,
        ocr_lang: str,
        ocr_mode: str,
        ocr_max: int,
        ocr_edge_threshold: float,
        ocr_downscale: int,
        hash_media: bool,
    ):
        self.folder = folder
        self.out_dir = out_dir
        self.resume = resume
        self.manifest = manifest
        self.stats = stats

        self.convert_audio = convert_audio
        self.transcriber = transcriber
        self.transcribe_lang = transcribe_lang

        self.ocr_enabled = ocr_enabled
        self.ocr_lang = ocr_lang
        self.ocr_mode = ocr_mode
        self.ocr_max = ocr_max
        self.ocr_edge_threshold = ocr_edge_threshold
        self.ocr_downscale = ocr_downscale

        self.hash_media = hash_media

        self.transcripts_dir = out_dir / "transcripts"
        self.ocr_dir = out_dir / "ocr"
        self.converted_dir = out_dir / "converted"

        # Concurrency controls
        self._transcribe_lock = threading.Lock()  # avoid backend thread-safety issues
        self._ocr_lock = threading.Lock()
        self._ocr_new = 0
        self._stats_lock = threading.Lock()

    def _inc(self, key: str, delta: int = 1) -> None:
        with self._stats_lock:
            self.stats[key] += delta

    def ensure_audio(self, file_name: str) -> None:
        src = self.folder / file_name
        if not src.exists():
            self._inc("missing_files")
            self.manifest.log({"type": "missing_file", "file": file_name, "kind": "audio"})
            return

        stem = Path(file_name).stem
        wav_created_this_run = False

        # Conversion requested
        if self.convert_audio == "mp3":
            dst = self.converted_dir / (stem + ".mp3")
            if self.resume and dst.exists():
                self._inc("audio_mp3_skipped")
                self.manifest.log({"type": "audio_mp3_skipped", "file": file_name, "out": relpath_posix(dst, self.out_dir)})
            else:
                convert_to_mp3(src, dst)
                self._inc("audio_mp3_created")
                self.manifest.log({"type": "audio_mp3_created", "file": file_name, "out": relpath_posix(dst, self.out_dir)})

        elif self.convert_audio == "wav":
            dst = self.converted_dir / (stem + ".wav")
            if self.resume and dst.exists():
                self._inc("audio_wav_skipped")
                self.manifest.log({"type": "audio_wav_skipped", "file": file_name, "out": relpath_posix(dst, self.out_dir)})
            else:
                convert_to_wav(src, dst)
                self._inc("audio_wav_created")
                wav_created_this_run = True
                self.manifest.log({"type": "audio_wav_created", "file": file_name, "out": relpath_posix(dst, self.out_dir)})

        # Transcript
        if self.transcriber is not None:
            tfile = self.transcripts_dir / (stem + ".txt")
            if self.resume and tfile.exists():
                self._inc("audio_transcripts_skipped")
                self.manifest.log({"type": "audio_transcript_skipped", "file": file_name, "out": relpath_posix(tfile, self.out_dir)})
                return

            try:
                wav = self.converted_dir / (stem + ".wav")
                if (not wav.exists()) or (not self.resume and not wav_created_this_run):
                    convert_to_wav(src, wav)
                    self._inc("audio_wav_created")
                    wav_created_this_run = True
                    self.manifest.log({"type": "audio_wav_created", "file": file_name, "out": relpath_posix(wav, self.out_dir)})
                else:
                    self._inc("audio_wav_skipped")

                with self._transcribe_lock:
                    text = self.transcriber.transcribe_wav(wav, language=self.transcribe_lang)

                atomic_write_text(tfile, text + "\n")
                self._inc("audio_transcripts_created")
                self.manifest.log({"type": "audio_transcript_created", "file": file_name, "out": relpath_posix(tfile, self.out_dir)})
            except Exception as e:
                self._inc("audio_transcripts_failed")
                self.manifest.log({"type": "audio_transcript_failed", "file": file_name, "error": str(e)})

        # Metadata (optional heavy)
        try:
            meta = compute_media_meta(src, "audio", do_hash=self.hash_media)
            self.manifest.log({"type": "media_meta", "file": file_name, "kind": "audio", "meta": asdict(meta)})
        except Exception:
            pass

    def ensure_image(self, file_name: str) -> None:
        src = self.folder / file_name
        if not src.exists():
            self._inc("missing_files")
            self.manifest.log({"type": "missing_file", "file": file_name, "kind": "image"})
            return

        if not self.ocr_enabled:
            return

        stem = Path(file_name).stem
        ofile = self.ocr_dir / (stem + ".txt")

        if self.resume and ofile.exists():
            self._inc("image_ocr_skipped")
            self.manifest.log({"type": "image_ocr_skipped", "file": file_name, "out": relpath_posix(ofile, self.out_dir)})
            return

        # Heuristic filter
        if self.ocr_mode != "all":
            ok = should_ocr_image(
                src,
                mode=self.ocr_mode,
                edge_threshold=self.ocr_edge_threshold,
                downscale=self.ocr_downscale,
            )
            if not ok:
                self._inc("image_ocr_filtered")
                self.manifest.log({"type": "image_ocr_filtered", "file": file_name, "mode": self.ocr_mode})
                return

        # OCR max per run (reserve a slot to enforce cap under concurrency)
        with self._ocr_lock:
            if self.ocr_max and self._ocr_new >= self.ocr_max:
                self._inc("image_ocr_deferred")
                self.manifest.log({"type": "image_ocr_deferred", "file": file_name, "reason": "ocr_max_reached"})
                return
            self._ocr_new += 1

        try:
            text = ocr_image(src, lang=self.ocr_lang)
            atomic_write_text(ofile, text + "\n")
            self._inc("image_ocr_created")
            self.manifest.log({"type": "image_ocr_created", "file": file_name, "out": relpath_posix(ofile, self.out_dir)})
        except Exception as e:
            self._inc("image_ocr_failed")
            self.manifest.log({"type": "image_ocr_failed", "file": file_name, "error": str(e)})

        # Metadata
        try:
            meta = compute_media_meta(src, "image", do_hash=self.hash_media)
            self.manifest.log({"type": "media_meta", "file": file_name, "kind": "image", "meta": asdict(meta)})
        except Exception:
            pass
