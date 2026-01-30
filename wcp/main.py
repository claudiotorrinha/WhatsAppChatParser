from __future__ import annotations

import concurrent.futures
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import load_config_from_argv
from .manifest import ManifestLogger
from .media import MediaProcessor
from .output import write_outputs
from .parser import count_total_messages, find_chat_txt, iter_messages
from .report import write_report
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

    folder_path = Path(folder_in).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Allow passing a WhatsApp export .zip directly.
    if folder_path.is_file() and folder_path.suffix.lower() == ".zip":
        zip_path = folder_path
        extract_dir = out_dir / "_extracted" / zip_path.stem

        if not args.quiet:
            sys.stderr.write(f"Extracting zip to: {extract_dir}\n")

        safe_extract_zip(zip_path, extract_dir)
        folder = find_export_root(extract_dir)
    else:
        folder = folder_path

    chat_txt = find_chat_txt(folder)

    resume = not args.no_resume

    manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=(not args.no_manifest))
    manifest.open()

    manifest.log({
        "type": "run_start",
        "chat_file": chat_txt.name,
        "export_folder": str(folder),
        "out_dir": str(out_dir),
        "resume": resume,
        "format": args.format,
        "date_order": args.date_order,
        "audio_workers": args.audio_workers,
        "ocr_workers": args.ocr_workers,
        "convert_audio": args.convert_audio,
        "transcribe": (not args.no_transcribe),
        "transcribe_backend": args.transcribe_backend,
        "whisper_model": args.whisper_model,
        "lang": args.lang,
        "ocr": (not args.no_ocr),
        "ocr_mode": args.ocr_mode,
        "ocr_max": args.ocr_max,
        "tz": args.tz,
    })

    total_msgs = None
    if not args.quiet:
        total_msgs = count_total_messages(chat_txt, args.format, args.date_order)
        sys.stderr.write(f"Chat file: {chat_txt.name}\n")
        sys.stderr.write(f"Detected messages: {total_msgs}\n")

    # Load messages into memory (for now) to allow multiple passes easily.
    # If this grows huge, we can stream with a lightweight index.
    messages = list(iter_messages(chat_txt, tz_offset=args.tz, format_override=args.format, date_order_override=args.date_order))

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
    if not args.no_transcribe and not args.only_ocr:
        t = Transcriber(args.whisper_model, backend=args.transcribe_backend)
        if t.available():
            transcriber = t
        else:
            if not args.quiet:
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
        convert_audio=("none" if args.only_transcribe else args.convert_audio),
        transcriber=(transcriber if not args.only_ocr else None),
        transcribe_lang=args.lang,
        ocr_enabled=(not args.no_ocr) and (not args.only_transcribe),
        ocr_lang=args.ocr_lang,
        ocr_mode=args.ocr_mode,
        ocr_max=args.ocr_max,
        ocr_edge_threshold=args.ocr_edge_threshold,
        ocr_downscale=args.ocr_downscale,
        hash_media=args.hash_media,
    )

    if not args.quiet:
        sys.stderr.write("Preprocessing media (resume-aware)...\n")

    t0 = time.time()

    # Audio pool
    def audio_job(fn: str):
        mp.ensure_audio(fn)

    # OCR pool
    def ocr_job(fn: str):
        mp.ensure_image(fn)

    # Limit tasks for only modes
    if args.only_transcribe:
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

    if args.only_ocr:
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.audio_workers)) as ex_a, \
         concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.ocr_workers)) as ex_o:

        futs = []
        fut_meta: dict[concurrent.futures.Future, tuple[str, str]] = {}
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
        progress_every = max(1, int(args.progress_every))

        for fut in concurrent.futures.as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                manifest.log({"type": "worker_exception", "error": str(e)})
            finally:
                done += 1
                if not args.quiet:
                    if done % progress_every == 0 or done == total_tasks:
                        kind, fn = fut_meta.get(fut, ("task", ""))
                        pct = (done / total_tasks * 100.0) if total_tasks else 100.0
                        sys.stderr.write(f"Media progress: {done}/{total_tasks} ({pct:.1f}%) — {kind}: {fn}\n")
                        sys.stderr.flush()

    preprocess_elapsed = time.time() - t0
    manifest.log({"type": "media_preprocess_done", "elapsed_seconds": preprocess_elapsed})

    # Outputs
    if not args.quiet:
        sys.stderr.write("Writing conversation outputs...\n")

    jsonl_path, md_path, by_month_dir = write_outputs(
        messages=messages,
        folder=folder,
        out_dir=out_dir,
        md_max_chars=args.md_max_chars,
        write_md=(not args.no_md),
        write_by_month=(not args.no_by_month),
        me=args.me,
        them=args.them,
        manifest=manifest,
    )

    # Report
    if not args.no_report:
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
            tz=args.tz,
            workers={"audio_workers": args.audio_workers, "ocr_workers": args.ocr_workers},
            participants=participants,
            me=args.me,
            them=args.them,
            date_range=(min_dt, max_dt),
            outputs={
                "conversation.jsonl": jsonl_path,
                "transcript.md": md_path or "(disabled)",
                "by-month": by_month_dir or "(disabled)",
                "manifest.jsonl": (out_dir / "manifest.jsonl") if not args.no_manifest else "(disabled)",
            },
            stats=stats,
        )

    elapsed = time.time() - t0
    manifest.log({"type": "run_end", "elapsed_seconds": elapsed, "stats": stats})
    manifest.close()

    if not args.quiet:
        sys.stderr.write("Done.\n")
        sys.stderr.write(f"  Media preprocess: {fmt_eta(preprocess_elapsed)}\n")
        sys.stderr.write(f"  Outputs: {jsonl_path}\n")
        sys.stderr.write(f"  Report: {out_dir / 'report.md'}\n" if not args.no_report else "")

    print(f"Wrote: {jsonl_path}")
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv))
