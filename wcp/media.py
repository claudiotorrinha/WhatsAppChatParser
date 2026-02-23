from __future__ import annotations

import inspect
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .manifest import ManifestLogger
from .models import MediaMeta
from .ocr_gate import should_ocr_image
from .transcript_quality import assess_transcript_quality, normalize_transcript_text
from .transcribe import Transcriber
from .util import (atomic_write_text, ffprobe_duration_seconds, image_dimensions,
                   media_artifact_stems, relpath_posix, sha256_file)


def _tmp_path_for(dst: Path) -> Path:
    return dst.with_name(dst.stem + ".tmp" + dst.suffix)


def _copy_to_tmp_then_replace(src: Path, dst: Path) -> None:
    tmp = _tmp_path_for(dst)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def ffmpeg_to_tmp_then_replace(cmd: list[str], dst: Path) -> None:
    # Ensure atomic output: write to dst.tmp then rename.
    tmp = _tmp_path_for(dst)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass
    cmd = cmd[:-1] + [str(tmp)]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if len(err) > 2000:
            err = err[-2000:]
        raise RuntimeError(f"ffmpeg failed (code {proc.returncode}). {err}")
    os.replace(tmp, dst)


def convert_to_wav(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-ac", "1",
        "-ar", "16000",
        str(dst),
    ]
    ffmpeg_to_tmp_then_replace(cmd, dst)


def convert_to_mp3(src: Path, dst: Path) -> None:
    # Fast path: passthrough when input is already MP3.
    if src.suffix.lower() == ".mp3":
        _copy_to_tmp_then_replace(src, dst)
        return

    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
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
        transcribe_lang: Optional[str],
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
        self._ocr_lock = threading.Lock()
        self._ocr_new = 0
        self._stats_lock = threading.Lock()
        self._transcribe_queue: Optional[queue.Queue[Optional[tuple[str, Path, Path]]]] = None
        self._transcribe_thread: Optional[threading.Thread] = None
        self._transcribe_state_lock = threading.Lock()
        self._transcribe_current_file: Optional[str] = None
        self._transcribe_current_started: Optional[float] = None
        self._transcribe_tasks_done = 0
        self._transcribe_finalizing = False
        self._quality_retry_lock = threading.Lock()
        self._quality_retry_candidates: dict[str, dict[str, Any]] = {}
        self._quality_retry_current_file: Optional[str] = None
        self._quality_retry_current_started: Optional[float] = None
        self._quality_retry_remaining = 0
        self._quality_retry_active = False
        self._transcribe_accepts_quality_retry = True

        if self.transcriber is not None:
            try:
                sig = inspect.signature(self.transcriber.transcribe_wav)
                params = list(sig.parameters.values())
                self._transcribe_accepts_quality_retry = (
                    ("quality_retry" in sig.parameters)
                    or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
                )
            except Exception:
                # Keep optimistic default; modern transcriber accepts quality_retry.
                self._transcribe_accepts_quality_retry = True
            self._transcribe_queue = queue.Queue()
            self._transcribe_thread = threading.Thread(target=self._transcribe_worker, daemon=True)
            self._transcribe_thread.start()

    def _inc(self, key: str, delta: int = 1) -> None:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + delta

    def _add_time(self, key: str, delta_seconds: float) -> None:
        with self._stats_lock:
            self.stats[key] = float(self.stats.get(key, 0.0)) + float(delta_seconds)

    @staticmethod
    def _first_existing(paths: list[Path]) -> Optional[Path]:
        for p in paths:
            if p.exists():
                return p
        return None

    @staticmethod
    def _artifact_paths(base_dir: Path, file_name: str, suffix: str) -> list[Path]:
        return [base_dir / (stem + suffix) for stem in media_artifact_stems(file_name)]

    def _transcribe_call(self, wav: Path, *, quality_retry: bool) -> str:
        if self.transcriber is None:
            raise RuntimeError("Transcriber unavailable.")
        if self._transcribe_accepts_quality_retry:
            return self.transcriber.transcribe_wav(
                wav,
                language=self.transcribe_lang,
                quality_retry=quality_retry,
            )
        return self.transcriber.transcribe_wav(wav, language=self.transcribe_lang)

    def _queue_quality_retry(
        self,
        file_name: str,
        wav: Path,
        tfile: Path,
        issues: list[str],
        *,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        baseline_token_count = 0
        if isinstance(metrics, dict):
            try:
                baseline_token_count = int(metrics.get("token_count", 0) or 0)
            except Exception:
                baseline_token_count = 0
        with self._quality_retry_lock:
            if file_name in self._quality_retry_candidates:
                return
            self._quality_retry_candidates[file_name] = {
                "wav": wav,
                "tfile": tfile,
                "issues": list(issues),
                "baseline_token_count": max(0, baseline_token_count),
            }
            self._quality_retry_remaining = len(self._quality_retry_candidates)

    def _run_quality_retries(self) -> None:
        if self.transcriber is None:
            return
        with self._quality_retry_lock:
            items = list(self._quality_retry_candidates.items())
            self._quality_retry_active = bool(items)
            self._quality_retry_remaining = len(items)

        if not items:
            return

        self.manifest.log({"type": "audio_transcript_retry_start", "count": len(items)})

        for file_name, info in items:
            wav = info.get("wav")
            tfile = info.get("tfile")
            before_issues = list(info.get("issues") or [])
            before_token_count = int(info.get("baseline_token_count", 0) or 0)
            if not isinstance(wav, Path) or not isinstance(tfile, Path):
                continue

            with self._quality_retry_lock:
                self._quality_retry_current_file = file_name
                self._quality_retry_current_started = time.time()
                self._quality_retry_remaining = max(0, self._quality_retry_remaining - 1)

            self._inc("audio_transcript_retry_attempted")
            try:
                started = time.perf_counter()
                retry_text = self._transcribe_call(wav, quality_retry=True)
                self._add_time("audio_transcript_retry_seconds", time.perf_counter() - started)
                retry_text = normalize_transcript_text(retry_text)
                retry_quality = assess_transcript_quality(retry_text)
                before_score = len(before_issues)
                after_score = len(retry_quality.issues)
                after_token_count = int(retry_quality.metrics.get("token_count", 0) or 0)
                # Guardrail: do not replace with a tiny low-quality fallback.
                improved = retry_quality.ok or ((after_score < before_score) and (after_token_count >= 8))

                if improved:
                    atomic_write_text(tfile, retry_text + "\n")
                    self._inc("audio_transcript_retry_succeeded")
                    self.manifest.log(
                        {
                            "type": "audio_transcript_retried",
                            "file": file_name,
                            "out": relpath_posix(tfile, self.out_dir),
                            "quality_before_issues": before_issues,
                            "quality_after_issues": retry_quality.issues,
                            "quality_after_metrics": retry_quality.metrics,
                            "quality_before_token_count": before_token_count,
                        }
                    )
                else:
                    self._inc("audio_transcript_retry_failed")
                    retry_reason = "quality_not_improved"
                    if after_score < before_score and after_token_count < 8:
                        retry_reason = "quality_improved_but_too_short"
                    self.manifest.log(
                        {
                            "type": "audio_transcript_retry_failed",
                            "file": file_name,
                            "error": retry_reason,
                            "quality_before_issues": before_issues,
                            "quality_after_issues": retry_quality.issues,
                            "quality_after_metrics": retry_quality.metrics,
                            "quality_before_token_count": before_token_count,
                        }
                    )

                if not retry_quality.ok:
                    self._inc("audio_transcript_retry_still_flagged")
                    self.manifest.log(
                        {
                            "type": "audio_transcript_quality_still_flagged",
                            "file": file_name,
                            "issues": retry_quality.issues,
                            "metrics": retry_quality.metrics,
                        }
                    )
            except Exception as e:
                self._inc("audio_transcript_retry_failed")
                self.manifest.log({"type": "audio_transcript_retry_failed", "file": file_name, "error": str(e)})
            finally:
                with self._quality_retry_lock:
                    self._quality_retry_current_file = None
                    self._quality_retry_current_started = None

        with self._quality_retry_lock:
            self._quality_retry_candidates.clear()
            self._quality_retry_remaining = 0
            self._quality_retry_active = False
            self._quality_retry_current_file = None
            self._quality_retry_current_started = None

        self.manifest.log({"type": "audio_transcript_retry_done", "count": len(items)})

    def _run_transcription(self, file_name: str, wav: Path, tfile: Path) -> None:
        if self.transcriber is None:
            return
        try:
            started = time.perf_counter()
            text = self._transcribe_call(wav, quality_retry=False)
            self._add_time("audio_transcribe_seconds", time.perf_counter() - started)
            text = normalize_transcript_text(text)
            quality = assess_transcript_quality(text)
            atomic_write_text(tfile, text + "\n")
            self._inc("audio_transcripts_created")
            self.manifest.log({"type": "audio_transcript_created", "file": file_name, "out": relpath_posix(tfile, self.out_dir)})
            if not quality.ok:
                self._inc("audio_transcript_quality_flagged")
                self.manifest.log(
                    {
                        "type": "audio_transcript_quality_flagged",
                        "file": file_name,
                        "issues": quality.issues,
                        "metrics": quality.metrics,
                        "retry_scheduled": True,
                    }
                )
                self._queue_quality_retry(
                    file_name,
                    wav,
                    tfile,
                    quality.issues,
                    metrics=quality.metrics,
                )
        except Exception as e:
            self._inc("audio_transcripts_failed")
            self.manifest.log({"type": "audio_transcript_failed", "file": file_name, "error": str(e)})

    def _transcribe_worker(self) -> None:
        if self._transcribe_queue is None:
            return
        while True:
            item = self._transcribe_queue.get()
            if item is None:
                self._transcribe_queue.task_done()
                return
            file_name, wav, tfile = item
            with self._transcribe_state_lock:
                self._transcribe_current_file = file_name
                self._transcribe_current_started = time.time()
            try:
                self._run_transcription(file_name, wav, tfile)
            finally:
                with self._transcribe_state_lock:
                    if self._transcribe_current_file == file_name:
                        self._transcribe_current_file = None
                        self._transcribe_current_started = None
                    self._transcribe_tasks_done += 1
                self._transcribe_queue.task_done()

    def transcription_status(self) -> dict:
        with self._quality_retry_lock:
            retry_active = self._quality_retry_active
            retry_current = self._quality_retry_current_file
            retry_started = self._quality_retry_current_started
            retry_remaining = self._quality_retry_remaining

        if self._transcribe_queue is None:
            if retry_active or retry_remaining > 0:
                elapsed = (time.time() - retry_started) if (retry_current is not None and retry_started is not None) else None
                return {
                    "enabled": True,
                    "pending": max(0, int(retry_remaining)),
                    "current": retry_current,
                    "current_elapsed_seconds": elapsed,
                    "done": self._transcribe_tasks_done,
                    "phase": "quality_retry",
                }
            return {
                "enabled": False,
                "pending": 0,
                "current": None,
                "current_elapsed_seconds": None,
                "done": 0,
                "phase": "idle",
            }

        try:
            pending = int(self._transcribe_queue.qsize())
        except Exception:
            pending = 0

        with self._transcribe_state_lock:
            current = self._transcribe_current_file
            started = self._transcribe_current_started
            done = self._transcribe_tasks_done
            finalizing = self._transcribe_finalizing

        if finalizing and pending > 0:
            # Hide finalize sentinel from user-visible backlog.
            pending -= 1
        elapsed = (time.time() - started) if (current is not None and started is not None) else None
        return {
            "enabled": True,
            "pending": max(0, pending),
            "current": current,
            "current_elapsed_seconds": elapsed,
            "done": done,
            "phase": "transcribe_queue",
        }

    def finalize(self) -> None:
        if self._transcribe_queue is None or self._transcribe_thread is None:
            self._run_quality_retries()
            return
        with self._transcribe_state_lock:
            self._transcribe_finalizing = True
        self._transcribe_queue.put(None)
        self._transcribe_queue.join()
        self._transcribe_thread.join(timeout=30.0)
        with self._transcribe_state_lock:
            self._transcribe_current_file = None
            self._transcribe_current_started = None
            self._transcribe_finalizing = False
        self._transcribe_queue = None
        self._transcribe_thread = None
        self._run_quality_retries()

    def ensure_audio(self, file_name: str) -> None:
        src = self.folder / file_name
        if not src.exists():
            self._inc("missing_files")
            self.manifest.log({"type": "missing_file", "file": file_name, "kind": "audio"})
            return

        wav_created_this_run = False
        wav_skip_counted_this_run = False
        wav_candidates = self._artifact_paths(self.converted_dir, file_name, ".wav")
        wav = wav_candidates[0]
        mp3_candidates = self._artifact_paths(self.converted_dir, file_name, ".mp3")
        tfile_candidates = self._artifact_paths(self.transcripts_dir, file_name, ".txt")
        tfile = tfile_candidates[0]
        existing_tfile = self._first_existing(tfile_candidates)

        def ensure_wav(*, count_skipped: bool) -> Optional[Path]:
            nonlocal wav_created_this_run, wav_skip_counted_this_run
            existing_wav = self._first_existing(wav_candidates)
            if existing_wav is not None:
                if count_skipped:
                    self._inc("audio_wav_skipped")
                    wav_skip_counted_this_run = True
                    self.manifest.log({"type": "audio_wav_skipped", "file": file_name, "out": relpath_posix(existing_wav, self.out_dir)})
                return existing_wav
            try:
                started = time.perf_counter()
                convert_to_wav(src, wav)
                self._add_time("audio_convert_seconds", time.perf_counter() - started)
                self._inc("audio_wav_created")
                wav_created_this_run = True
                self.manifest.log({"type": "audio_wav_created", "file": file_name, "out": relpath_posix(wav, self.out_dir)})
                return wav
            except Exception as e:
                self._inc("audio_wav_failed")
                self.manifest.log({"type": "audio_wav_failed", "file": file_name, "error": str(e)})
                return None

        transcript_needed = self.transcriber is not None and not (self.resume and existing_tfile is not None)

        # Conversion requested
        if self.convert_audio == "mp3":
            dst = mp3_candidates[0]
            existing_mp3 = self._first_existing(mp3_candidates)
            mp3_src = src

            # If transcript will run, prepare WAV once and reuse it for MP3 encoding.
            if transcript_needed:
                wav_for_mp3 = ensure_wav(count_skipped=False)
                if wav_for_mp3 is not None:
                    mp3_src = wav_for_mp3

            if self.resume and existing_mp3 is not None:
                self._inc("audio_mp3_skipped")
                self.manifest.log({"type": "audio_mp3_skipped", "file": file_name, "out": relpath_posix(existing_mp3, self.out_dir)})
            else:
                try:
                    started = time.perf_counter()
                    convert_to_mp3(mp3_src, dst)
                    self._add_time("audio_convert_seconds", time.perf_counter() - started)
                    self._inc("audio_mp3_created")
                    self.manifest.log({"type": "audio_mp3_created", "file": file_name, "out": relpath_posix(dst, self.out_dir)})
                except Exception as e:
                    self._inc("audio_mp3_failed")
                    self.manifest.log({"type": "audio_mp3_failed", "file": file_name, "error": str(e)})

        elif self.convert_audio == "wav":
            ensure_wav(count_skipped=True)

        # Transcript
        if self.transcriber is not None:
            if self.resume and existing_tfile is not None:
                self._inc("audio_transcripts_skipped")
                self.manifest.log({"type": "audio_transcript_skipped", "file": file_name, "out": relpath_posix(existing_tfile, self.out_dir)})
                try:
                    existing_text = existing_tfile.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    existing_text = ""
                existing_text = normalize_transcript_text(existing_text)
                existing_quality = assess_transcript_quality(existing_text)
                if not existing_quality.ok:
                    self._inc("audio_transcript_quality_flagged")
                    self.manifest.log(
                        {
                            "type": "audio_transcript_quality_flagged",
                            "file": file_name,
                            "issues": existing_quality.issues,
                            "metrics": existing_quality.metrics,
                            "source": "resume_existing",
                            "retry_scheduled": True,
                        }
                    )
                    wav_for_retry = ensure_wav(count_skipped=False)
                    if wav_for_retry is not None:
                        self._queue_quality_retry(
                            file_name,
                            wav_for_retry,
                            existing_tfile,
                            existing_quality.issues,
                            metrics=existing_quality.metrics,
                        )
                return

            should_count_wav_skip = (not wav_created_this_run) and (not wav_skip_counted_this_run)
            wav_for_transcribe = ensure_wav(count_skipped=should_count_wav_skip)
            if wav_for_transcribe is None:
                return

            if self._transcribe_queue is not None:
                self._transcribe_queue.put((file_name, wav_for_transcribe, tfile))
            else:
                self._run_transcription(file_name, wav_for_transcribe, tfile)

        # Metadata (optional heavy)
        if self.hash_media:
            try:
                started = time.perf_counter()
                meta = compute_media_meta(src, "audio", do_hash=self.hash_media)
                self._add_time("audio_meta_seconds", time.perf_counter() - started)
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

        ofile_candidates = self._artifact_paths(self.ocr_dir, file_name, ".txt")
        ofile = ofile_candidates[0]
        existing_ofile = self._first_existing(ofile_candidates)

        if self.resume and existing_ofile is not None:
            self._inc("image_ocr_skipped")
            self.manifest.log({"type": "image_ocr_skipped", "file": file_name, "out": relpath_posix(existing_ofile, self.out_dir)})
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
            started = time.perf_counter()
            text = ocr_image(src, lang=self.ocr_lang)
            self._add_time("image_ocr_seconds", time.perf_counter() - started)
            atomic_write_text(ofile, text + "\n")
            self._inc("image_ocr_created")
            self.manifest.log({"type": "image_ocr_created", "file": file_name, "out": relpath_posix(ofile, self.out_dir)})
        except Exception as e:
            self._inc("image_ocr_failed")
            self.manifest.log({"type": "image_ocr_failed", "file": file_name, "error": str(e)})

        # Metadata
        try:
            started = time.perf_counter()
            meta = compute_media_meta(src, "image", do_hash=self.hash_media)
            self._add_time("image_meta_seconds", time.perf_counter() - started)
            self.manifest.log({"type": "media_meta", "file": file_name, "kind": "image", "meta": asdict(meta)})
        except Exception:
            pass
