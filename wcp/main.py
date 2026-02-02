from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import load_config_from_argv
from .manifest import ManifestLogger
from .media import MediaProcessor
from .output import write_outputs
from .parser import count_total_messages, find_chat_txt, iter_messages
from .report import write_report
from .run_config import RunConfig
from .transcribe import Transcriber
from .util import fmt_eta, now_utc_iso
from .ziputil import safe_extract_zip, find_export_root


def build_arg_parser(config: dict) -> "argparse.ArgumentParser":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="Path to JSON config (optional)")

    ap.add_argument("folder", nargs="?", default=config.get("folder"),
                    help="Folder containing WhatsApp export .txt + media files (if omitted, you'll be prompted)")

    ap.add_argument("--tz", default=config.get("tz", "+00:00"),
                    help="Timezone offset for timestamps, e.g. +00:00 or +01:00")
    ap.add_argument("--out", default=config.get("out", "out"), help="Output folder")

    ap.add_argument("--quiet", action="store_true", default=bool(config.get("quiet", False)),
                    help="Disable progress output")
    ap.add_argument("--progress-every", type=int, default=int(config.get("progress_every", 50)),
                    help="Print progress every N messages")

    ap.add_argument("--format", choices=["auto", "pt", "android", "ios"],
                    default=config.get("format", "auto"),
                    help="Force export format (default auto-detect)")
    ap.add_argument("--date-order", choices=["auto", "dmy", "mdy"],
                    default=config.get("date_order", "auto"),
                    help="Force date order for android/ios formats (default auto-detect)")

    ap.add_argument("--no-resume", action="store_true", default=bool(config.get("no_resume", False)),
                    help="Recompute conversions/transcripts/OCR even if output files already exist")
    ap.add_argument("--no-manifest", action="store_true", default=bool(config.get("no_manifest", False)),
                    help="Disable writing out/manifest.jsonl")
    ap.add_argument("--no-report", action="store_true", default=bool(config.get("no_report", False)),
                    help="Disable writing out/report.md")

    ap.add_argument("--no-md", action="store_true", default=bool(config.get("no_md", False)),
                    help="Disable writing out/transcript.md")
    ap.add_argument("--md-max-chars", type=int, default=int(config.get("md_max_chars", 4000)),
                    help="Max chars to include per transcript/OCR field in transcript.md")

    ap.add_argument("--no-by-month", action="store_true", default=bool(config.get("no_by_month", False)),
                    help="Disable writing out/by-month/*.jsonl and *.md")

    # Worker pools
    cpu = os.cpu_count() or 4
    ap.add_argument("--audio-workers", type=int, default=int(config.get("audio_workers", min(2, cpu))),
                    help="Worker threads for audio conversion/transcription")
    ap.add_argument("--ocr-workers", type=int, default=int(config.get("ocr_workers", min(2, cpu))),
                    help="Worker threads for image OCR")

    ap.add_argument("--hash-media", action="store_true", default=bool(config.get("hash_media", False)),
                    help="Compute sha256 for media files (slower)")

    ap.add_argument("--me", action="append", default=list(config.get("me", [])),
                    help="Exact sender name(s) to map to sender_id=ME (repeatable)")
    ap.add_argument("--them", action="append", default=list(config.get("them", [])),
                    help="Exact sender name(s) to map to sender_id=THEM (repeatable)")

    ap.add_argument("--convert-audio", choices=["none", "mp3", "wav"], default=config.get("convert_audio", "mp3"),
                    help="Convert voice notes to mp3 or wav")

    ap.add_argument("--no-transcribe", action="store_true", default=bool(config.get("no_transcribe", False)),
                    help="Disable audio transcription")
    ap.add_argument("--whisper-model", default=config.get("whisper_model", "small"),
                    help="Whisper model name: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default=config.get("lang", "pt"), help="Transcription language")
    ap.add_argument("--transcribe-backend", choices=["openai", "auto", "faster"], default=config.get("transcribe_backend", "openai"),
                    help="Transcription backend (default openai)")

    ap.add_argument("--no-ocr", action="store_true", default=bool(config.get("no_ocr", False)),
                    help="Disable image OCR")
    ap.add_argument("--ocr-lang", default=config.get("ocr_lang", "por"), help="Tesseract OCR language")
    ap.add_argument("--ocr-mode", choices=["all", "likely-text"], default=config.get("ocr_mode", "all"),
                    help="OCR mode")
    ap.add_argument("--ocr-max", type=int, default=int(config.get("ocr_max", 0)),
                    help="Max number of NEW OCR operations per run")
    ap.add_argument("--ocr-edge-threshold", type=float, default=float(config.get("ocr_edge_threshold", 18.0)),
                    help="Edge-density threshold for likely-text")
    ap.add_argument("--ocr-downscale", type=int, default=int(config.get("ocr_downscale", 512)),
                    help="Downscale max dimension for OCR heuristic")

    ap.add_argument("--only-transcribe", action="store_true", default=bool(config.get("only_transcribe", False)),
                    help="Only run missing audio transcripts (skip OCR and audio mp3/wav conversions unless needed)")
    ap.add_argument("--only-ocr", action="store_true", default=bool(config.get("only_ocr", False)),
                    help="Only run missing image OCR")

    return ap


def run(argv: list[str]) -> int:
    config = load_config_from_argv(argv)
    ap = build_arg_parser(config)
    args = ap.parse_args(argv[1:])

    folder_in = args.folder
    if not folder_in:
        folder_in = input("Path to WhatsApp export folder OR .zip (txt + media): ").strip().strip('"')
        args.folder = folder_in

    cfg = RunConfig.from_args(args)
    errors = cfg.validate()
    if errors:
        raise SystemExit("Invalid config: " + "; ".join(errors))

    folder_path = Path(cfg.folder).expanduser().resolve()
    out_dir = Path(cfg.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Allow passing a WhatsApp export .zip directly.
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

    resume = not cfg.no_resume

    manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=(not cfg.no_manifest))
    manifest.open()

    manifest.log({
        "type": "run_start",
        "chat_file": chat_txt.name,
        "export_folder": str(folder),
        "out_dir": str(out_dir),
        "resume": resume,
        "format": cfg.format,
        "date_order": cfg.date_order,
        "audio_workers": cfg.audio_workers,
        "ocr_workers": cfg.ocr_workers,
        "convert_audio": cfg.convert_audio,
        "transcribe": (not cfg.no_transcribe),
        "transcribe_backend": cfg.transcribe_backend,
        "whisper_model": cfg.whisper_model,
        "lang": cfg.lang,
        "ocr": (not cfg.no_ocr),
        "ocr_mode": cfg.ocr_mode,
        "ocr_max": cfg.ocr_max,
        "tz": cfg.tz,
    })

    total_msgs = None
    if not cfg.quiet:
        total_msgs = count_total_messages(chat_txt, cfg.format, cfg.date_order)
        sys.stderr.write(f"Chat file: {chat_txt.name}\n")
        sys.stderr.write(f"Detected messages: {total_msgs}\n")

    # Load messages into memory (for now) to allow multiple passes easily.
    # If this grows huge, we can stream with a lightweight index.
    messages = list(iter_messages(chat_txt, tz_offset=cfg.tz, format_override=cfg.format, date_order_override=cfg.date_order))

    # Gather media refs
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

    # Transcriber (quality-first)
    transcriber = None
    if not cfg.no_transcribe and not cfg.only_ocr:
        t = Transcriber(cfg.whisper_model, backend=cfg.transcribe_backend)
        if t.available():
            transcriber = t
        else:
            if not cfg.quiet:
                sys.stderr.write(
                    "WARNING: transcription enabled but no backend installed. Install openai-whisper (recommended).\n"
                )
            manifest.log({"type": "transcriber_unavailable"})

    # Phase: media processing
    mp = MediaProcessor(
        folder=folder,
        out_dir=out_dir,
        resume=resume,
        manifest=manifest,
        stats=stats,
        convert_audio=("none" if cfg.only_transcribe else cfg.convert_audio),
        transcriber=(transcriber if not cfg.only_ocr else None),
        transcribe_lang=(None if cfg.lang == "auto" else cfg.lang),
        ocr_enabled=(not cfg.no_ocr) and (not cfg.only_transcribe),
        ocr_lang=("por+eng" if cfg.ocr_lang == "auto" else cfg.ocr_lang),
        ocr_mode=cfg.ocr_mode,
        ocr_max=cfg.ocr_max,
        ocr_edge_threshold=cfg.ocr_edge_threshold,
        ocr_downscale=cfg.ocr_downscale,
        hash_media=cfg.hash_media,
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

    # Audio pool
    def audio_job(fn: str):
        with state_lock:
            current["audio"] = fn
            current["audio_start"] = time.time()
        log_line(f"Media start: audio: {fn}")
        mp.ensure_audio(fn)
        with state_lock:
            if current.get("audio") == fn:
                current["audio"] = None
                current["audio_start"] = None

    # OCR pool
    def ocr_job(fn: str):
        with state_lock:
            current["image"] = fn
            current["image_start"] = time.time()
        log_line(f"Media start: image: {fn}")
        mp.ensure_image(fn)
        with state_lock:
            if current.get("image") == fn:
                current["image"] = None
                current["image_start"] = None

    # Limit tasks for only modes
    if cfg.only_transcribe:
        # Only process audios that are missing transcripts
        filtered = []
        for fn in audio_files:
            stem = Path(fn).stem
            tfile = out_dir / "transcripts" / (stem + ".txt")
            if resume and tfile.exists():
                continue
            filtered.append(fn)
        audio_files = filtered
        image_files = []

    if cfg.only_ocr:
        # Only process images missing OCR
        filtered = []
        for fn in image_files:
            stem = Path(fn).stem
            ofile = out_dir / "ocr" / (stem + ".txt")
            if resume and ofile.exists():
                continue
            filtered.append(fn)
        image_files = filtered
        audio_files = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, cfg.audio_workers)) as ex_a, \
         concurrent.futures.ThreadPoolExecutor(max_workers=max(1, cfg.ocr_workers)) as ex_o:

        futs = []
        fut_meta: dict[concurrent.futures.Future, tuple[str, str]] = {}
        done_lock = threading.Lock()
        done_ref = {"count": 0}
        for fn in audio_files:
            fut = ex_a.submit(audio_job, fn)
            futs.append(fut)
            fut_meta[fut] = ("audio", fn)
        for fn in image_files:
            fut = ex_o.submit(ocr_job, fn)
            futs.append(fut)
            fut_meta[fut] = ("image", fn)

        total_tasks = len(futs)
        done = 0
        progress_every = max(1, int(cfg.progress_every))

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
                if cur_audio:
                    item_elapsed = fmt_eta(time.time() - audio_start) if audio_start else "?"
                    cur = f"audio: {cur_audio} ({item_elapsed})"
                elif cur_image:
                    item_elapsed = fmt_eta(time.time() - image_start) if image_start else "?"
                    cur = f"image: {cur_image} ({item_elapsed})"
                else:
                    cur = "idle"
                log_line(f"Media heartbeat: {c}/{total_tasks} ({pct:.1f}%) — current {cur} — elapsed {fmt_eta(time.time() - t0)}")

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
                if not cfg.quiet:
                    if done % progress_every == 0 or done == total_tasks:
                        kind, fn = fut_meta.get(fut, ("task", ""))
                        pct = (done / total_tasks * 100.0) if total_tasks else 100.0
                        log_line(f"Media progress: {done}/{total_tasks} ({pct:.1f}%) — {kind}: {fn}")

        heartbeat_stop.set()
        try:
            hb_thread.join(timeout=1.0)
        except Exception:
            pass

    preprocess_elapsed = time.time() - t0
    manifest.log({"type": "media_preprocess_done", "elapsed_seconds": preprocess_elapsed})

    # Outputs
    if not cfg.quiet:
        sys.stderr.write("Writing conversation outputs...\n")

    jsonl_path, md_path, by_month_dir = write_outputs(
        messages=messages,
        folder=folder,
        out_dir=out_dir,
        md_max_chars=cfg.md_max_chars,
        write_md=(not cfg.no_md),
        write_by_month=(not cfg.no_by_month),
        me=cfg.me,
        them=cfg.them,
        manifest=manifest,
    )

    # Report
    if not cfg.no_report:
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
            tz=cfg.tz,
            workers={"audio_workers": cfg.audio_workers, "ocr_workers": cfg.ocr_workers},
            participants=participants,
            me=cfg.me,
            them=cfg.them,
            date_range=(min_dt, max_dt),
            outputs={
                "conversation.jsonl": jsonl_path,
                "transcript.md": md_path or "(disabled)",
                "by-month": by_month_dir or "(disabled)",
                "manifest.jsonl": (out_dir / "manifest.jsonl") if not cfg.no_manifest else "(disabled)",
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
        sys.stderr.write(f"  Report: {out_dir / 'report.md'}\n" if not cfg.no_report else "")

    print(f"Wrote: {jsonl_path}")
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv))
