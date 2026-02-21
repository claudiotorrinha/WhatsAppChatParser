from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .manifest import ManifestLogger
from .media import MediaProcessor
from .output import write_outputs
from .parser import count_total_messages, find_chat_txt, iter_messages, resolve_tz_offset_str
from .report import write_report
from .run_config import RunConfig, SUPPORTED_WHISPER_MODELS
from .transcribe import Transcriber
from .util import fmt_eta
from .ziputil import find_export_root, safe_extract_zip

DEFAULT_TZ = "auto"
DEFAULT_FORMAT = "auto"
DEFAULT_DATE_ORDER = "auto"
DEFAULT_MD_MAX_CHARS = 4000
DEFAULT_AUDIO_CONVERT = "mp3"
DEFAULT_OCR_LANG = "por+eng"
DEFAULT_OCR_MODE = "all"
DEFAULT_OCR_MAX = 0
DEFAULT_OCR_EDGE_THRESHOLD = 18.0
DEFAULT_OCR_DOWNSCALE = 512

# Compatibility shim: accepted for one release and ignored.
_LEGACY_FLAGS_WITH_VALUE = {
    "--config",
    "--tz",
    "--progress-every",
    "--format",
    "--date-order",
    "--md-max-chars",
    "--audio-workers",
    "--ocr-workers",
    "--me",
    "--them",
    "--convert-audio",
    "--lang",
    "--transcribe-backend",
    "--ocr-lang",
    "--ocr-mode",
    "--ocr-max",
    "--ocr-edge-threshold",
    "--ocr-downscale",
}
_LEGACY_FLAGS_NO_VALUE = {
    "--no-resume",
    "--no-manifest",
    "--no-report",
    "--no-md",
    "--no-by-month",
    "--hash-media",
    "--only-transcribe",
    "--only-ocr",
}


def _auto_workers() -> int:
    return max(1, min(4, os.cpu_count() or 4))


def _strip_legacy_args(args: list[str]) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    ignored: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            cleaned.extend(args[i:])
            break

        flag, has_eq, _ = token.partition("=")
        if flag in _LEGACY_FLAGS_WITH_VALUE:
            ignored.append(flag)
            if has_eq:
                i += 1
            else:
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    i += 2
                else:
                    i += 1
            continue

        if flag in _LEGACY_FLAGS_NO_VALUE:
            ignored.append(flag)
            i += 1
            continue

        cleaned.append(token)
        i += 1

    return cleaned, list(dict.fromkeys(ignored))


def build_arg_parser() -> "argparse.ArgumentParser":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="Folder containing WhatsApp export .txt + media files, or a .zip")
    ap.add_argument("--out", default="out", help="Output folder")
    ap.add_argument("--quiet", action="store_true", help="Disable progress output")
    ap.add_argument("--force-cpu", action="store_true", help="Force CPU even if CUDA is available")
    ap.add_argument("--no-transcribe", action="store_true", help="Disable audio transcription")
    ap.add_argument(
        "--whisper-model",
        choices=list(SUPPORTED_WHISPER_MODELS),
        default="medium",
        help="Transcription model name",
    )
    ap.add_argument("--no-ocr", action="store_true", help="Disable image OCR")
    return ap


def run(argv: list[str]) -> int:
    ap = build_arg_parser()
    cleaned_args, ignored_flags = _strip_legacy_args(argv[1:])
    if ignored_flags:
        sys.stderr.write(
            "WARNING: Ignoring deprecated options: "
            + ", ".join(ignored_flags)
            + ". These options will be removed in a future release.\n"
        )
    args = ap.parse_args(cleaned_args)

    cfg = RunConfig.from_args(args)
    errors = cfg.validate()
    if errors:
        raise SystemExit("Invalid config: " + "; ".join(errors))
    effective_tz = resolve_tz_offset_str(DEFAULT_TZ)

    folder_path = Path(cfg.folder).expanduser().resolve()
    out_dir = Path(cfg.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if folder_path.is_file() and folder_path.suffix.lower() == ".zip":
        zip_path = folder_path
        extract_dir = out_dir / "_extracted" / zip_path.stem
        if not cfg.quiet:
            sys.stderr.write(f"Extracting zip to: {extract_dir}\n")
        safe_extract_zip(zip_path, extract_dir)
        folder = find_export_root(extract_dir)
    else:
        folder = folder_path

    chat_txt = find_chat_txt(folder)
    audio_workers = _auto_workers()
    ocr_workers = _auto_workers()
    resume = True

    manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=True)
    manifest.open()

    manifest.log(
        {
            "type": "run_start",
            "chat_file": chat_txt.name,
            "export_folder": str(folder),
            "out_dir": str(out_dir),
            "resume": resume,
            "format": DEFAULT_FORMAT,
            "date_order": DEFAULT_DATE_ORDER,
            "audio_workers": audio_workers,
            "ocr_workers": ocr_workers,
            "convert_audio": ("none" if cfg.no_transcribe else DEFAULT_AUDIO_CONVERT),
            "transcribe": (not cfg.no_transcribe),
            "whisper_model": cfg.whisper_model,
            "force_cpu": cfg.force_cpu,
            "lang": "auto",
            "ocr": (not cfg.no_ocr),
            "ocr_mode": DEFAULT_OCR_MODE,
            "ocr_max": DEFAULT_OCR_MAX,
            "tz": effective_tz,
        }
    )

    total_msgs = None
    if not cfg.quiet:
        total_msgs = count_total_messages(chat_txt, DEFAULT_FORMAT, DEFAULT_DATE_ORDER)
        sys.stderr.write(f"Chat file: {chat_txt.name}\n")
        sys.stderr.write(f"Detected messages: {total_msgs}\n")

    messages = list(
        iter_messages(
            chat_txt,
            tz_offset=effective_tz,
            format_override=DEFAULT_FORMAT,
            date_order_override=DEFAULT_DATE_ORDER,
        )
    )

    audio_files = sorted({m.file for msg in messages for m in msg.media if m.kind == "audio"})
    image_files = sorted({m.file for msg in messages for m in msg.media if m.kind == "image"})

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
    }

    transcriber = None
    if not cfg.no_transcribe:
        t = Transcriber(cfg.whisper_model, device=("cpu" if cfg.force_cpu else None))
        if t.available():
            transcriber = t
        else:
            reason = t.backend_error() or "unknown"
            if not cfg.quiet:
                sys.stderr.write(
                    "WARNING: transcription enabled but HF backend is unavailable: "
                    + reason
                    + "\n"
                )
            manifest.log({"type": "transcriber_unavailable", "reason": reason})

    mp = MediaProcessor(
        folder=folder,
        out_dir=out_dir,
        resume=resume,
        manifest=manifest,
        stats=stats,
        convert_audio=("none" if cfg.no_transcribe else DEFAULT_AUDIO_CONVERT),
        transcriber=transcriber,
        transcribe_lang=None,
        ocr_enabled=(not cfg.no_ocr),
        ocr_lang=DEFAULT_OCR_LANG,
        ocr_mode=DEFAULT_OCR_MODE,
        ocr_max=DEFAULT_OCR_MAX,
        ocr_edge_threshold=DEFAULT_OCR_EDGE_THRESHOLD,
        ocr_downscale=DEFAULT_OCR_DOWNSCALE,
        hash_media=False,
    )

    if not cfg.quiet:
        sys.stderr.write("Preprocessing media (resume-aware)...\n")

    t0 = time.time()
    log_lock = threading.Lock()
    state_lock = threading.Lock()
    current = {"audio": None, "image": None, "audio_start": None, "image_start": None}

    def log_line(msg: str) -> None:
        if cfg.quiet:
            return
        with log_lock:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def audio_job(fn: str):
        started = time.time()
        with state_lock:
            current["audio"] = fn
            current["audio_start"] = started
        log_line(f"Processing audio: {fn}")
        try:
            mp.ensure_audio(fn)
        finally:
            elapsed = fmt_eta(time.time() - started)
            log_line(f"Finished audio: {fn} ({elapsed})")
            with state_lock:
                if current.get("audio") == fn:
                    current["audio"] = None
                    current["audio_start"] = None

    def ocr_job(fn: str):
        started = time.time()
        with state_lock:
            current["image"] = fn
            current["image_start"] = started
        log_line(f"Processing image: {fn}")
        try:
            mp.ensure_image(fn)
        finally:
            elapsed = fmt_eta(time.time() - started)
            log_line(f"Finished image: {fn} ({elapsed})")
            with state_lock:
                if current.get("image") == fn:
                    current["image"] = None
                    current["image_start"] = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=audio_workers) as ex_a, concurrent.futures.ThreadPoolExecutor(
        max_workers=ocr_workers
    ) as ex_o:
        futs = []
        done_lock = threading.Lock()
        done_ref = {"count": 0}
        for fn in audio_files:
            fut = ex_a.submit(audio_job, fn)
            futs.append(fut)
        for fn in image_files:
            fut = ex_o.submit(ocr_job, fn)
            futs.append(fut)

        total_tasks = len(futs)
        done = 0
        heartbeat_stop = threading.Event()

        def heartbeat():
            if cfg.quiet:
                return
            while not heartbeat_stop.wait(30):
                with done_lock:
                    c = done_ref["count"]
                pct = (c / total_tasks * 100.0) if total_tasks else 100.0
                with state_lock:
                    cur_audio = current.get("audio")
                    cur_image = current.get("image")
                    audio_start = current.get("audio_start")
                    image_start = current.get("image_start")
                running_parts: list[str] = []
                if cur_audio:
                    item_elapsed = fmt_eta(time.time() - audio_start) if audio_start else "?"
                    running_parts.append(f"audio={cur_audio} ({item_elapsed})")
                if cur_image:
                    item_elapsed = fmt_eta(time.time() - image_start) if image_start else "?"
                    running_parts.append(f"image={cur_image} ({item_elapsed})")
                running_desc = " | ".join(running_parts) if running_parts else "idle"
                log_line(f"Running OK: {c}/{total_tasks} done ({pct:.1f}%) | {running_desc}")

        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()

        for fut in concurrent.futures.as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                manifest.log({"type": "worker_exception", "error": str(e)})
            finally:
                done += 1
                with done_lock:
                    done_ref["count"] = done

        heartbeat_stop.set()
        try:
            hb_thread.join(timeout=1.0)
        except Exception:
            pass

    preprocess_elapsed = time.time() - t0
    manifest.log({"type": "media_preprocess_done", "elapsed_seconds": preprocess_elapsed})

    if not cfg.quiet:
        sys.stderr.write("Writing conversation outputs...\n")

    jsonl_path, md_path, by_month_dir = write_outputs(
        messages=messages,
        folder=folder,
        out_dir=out_dir,
        md_max_chars=DEFAULT_MD_MAX_CHARS,
        write_md=True,
        write_by_month=True,
        me=[],
        them=[],
        manifest=manifest,
    )

    participants = sorted({m.sender for m in messages if m.sender})
    min_dt = None
    max_dt = None
    for msg in messages:
        try:
            dt = datetime.fromisoformat(msg.ts)
        except Exception:
            continue
        min_dt = dt if (min_dt is None or dt < min_dt) else min_dt
        max_dt = dt if (max_dt is None or dt > max_dt) else max_dt

    write_report(
        path=out_dir / "report.md",
        chat_file=chat_txt.name,
        export_folder=folder,
        out_dir=out_dir,
        resume=resume,
        tz=effective_tz,
        workers={"audio_workers": audio_workers, "ocr_workers": ocr_workers},
        participants=participants,
        me=[],
        them=[],
        date_range=(min_dt, max_dt),
        outputs={
            "conversation.jsonl": jsonl_path,
            "transcript.md": md_path or "(disabled)",
            "by-month": by_month_dir or "(disabled)",
            "manifest.jsonl": (out_dir / "manifest.jsonl"),
        },
        stats=stats,
    )

    elapsed = time.time() - t0
    manifest.log({"type": "run_end", "elapsed_seconds": elapsed, "stats": stats})
    manifest.close()

    if not cfg.quiet:
        sys.stderr.write("Done.\n")
        sys.stderr.write(f"  Media preprocess: {fmt_eta(preprocess_elapsed)}\n")
        sys.stderr.write(f"  Outputs: {jsonl_path}\n")
        sys.stderr.write(f"  Report: {out_dir / 'report.md'}\n")

    print(f"Wrote: {jsonl_path}")
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv))
