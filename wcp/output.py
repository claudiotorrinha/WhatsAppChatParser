from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .manifest import ManifestLogger
from .models import Enrichment, Message
from .util import clip, relpath_posix


def sender_to_id(sender: Optional[str], me: list[str], them: list[str]) -> Optional[str]:
    if sender is None:
        return None
    s = sender.strip()
    if me and any(s.lower() == x.lower() for x in me):
        return "ME"
    if them and any(s.lower() == x.lower() for x in them):
        return "THEM"
    return s


def _read_last_jsonl_line(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return None
            buf = b""
            pos = end
            while pos > 0:
                step = min(4096, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if b"\n" in buf:
                    break
            for line in reversed(buf.splitlines()):
                if line.strip():
                    return line.decode("utf-8", errors="replace")
    except Exception:
        return None
    return None


def _read_last_marker(path: Path) -> Optional[tuple[str, Optional[str]]]:
    line = _read_last_jsonl_line(path)
    if not line:
        return None
    try:
        obj = json.loads(line)
        ts = obj.get("ts")
        source_line = obj.get("source_line")
        if isinstance(ts, str):
            return ts, (source_line if isinstance(source_line, str) else None)
    except Exception:
        return None
    return None


def write_outputs(
    *,
    messages: list[Message],
    folder: Path,
    out_dir: Path,
    md_max_chars: int,
    write_md: bool,
    write_by_month: bool,
    me: list[str],
    them: list[str],
    manifest: ManifestLogger,
) -> tuple[Path, Optional[Path], Optional[Path]]:
    jsonl_path = out_dir / "conversation.jsonl"
    md_path = out_dir / "transcript.md"
    by_month_dir = out_dir / "by-month"

    transcripts_dir = out_dir / "transcripts"
    ocr_dir = out_dir / "ocr"
    converted_dir = out_dir / "converted"

    md_f = None
    last_month = None
    if write_md:
        md_f = md_path.open("w", encoding="utf-8")
        md_f.write("# WhatsApp Transcript\n\n")

    by_month_jsonl: dict[str, object] = {}
    by_month_md: dict[str, object] = {}
    month_marker_index: dict[str, int] = {}
    if write_by_month:
        by_month_dir.mkdir(parents=True, exist_ok=True)
        marker_to_index: dict[tuple[str, Optional[str]], int] = {}
        for i, msg in enumerate(messages):
            if msg.ts and msg.source_line:
                marker_to_index[(msg.ts, msg.source_line)] = i

        month_keys: set[str] = set()
        for msg in messages:
            try:
                dt = datetime.fromisoformat(msg.ts)
            except Exception:
                dt = None
            month_key = dt.strftime("%Y-%m") if dt else "unknown"
            month_keys.add(month_key)

        for month_key in month_keys:
            marker_path = by_month_dir / f"{month_key}.jsonl"
            marker = _read_last_marker(marker_path)
            if marker:
                idx = marker_to_index.get(marker)
                if idx is not None:
                    month_marker_index[month_key] = idx
                else:
                    manifest.log({
                        "type": "by_month_marker_missing",
                        "month": month_key,
                        "marker": marker,
                        "file": str(marker_path),
                    })

    def get_month_handles(month_key: str):
        if not write_by_month:
            return None, None
        jf = by_month_jsonl.get(month_key)
        mf = by_month_md.get(month_key)
        if jf is None:
            jf = (by_month_dir / f"{month_key}.jsonl").open("a", encoding="utf-8")
            by_month_jsonl[month_key] = jf
        if mf is None:
            md_path = by_month_dir / f"{month_key}.md"
            write_header = (not md_path.exists()) or md_path.stat().st_size == 0
            mf = md_path.open("a", encoding="utf-8")
            by_month_md[month_key] = mf
            if write_header:
                mf.write(f"# {month_key}\n\n")
        return jf, mf

    with jsonl_path.open("w", encoding="utf-8") as f_out:
        for i, msg in enumerate(messages):
            # Enrichment attach
            enr = Enrichment()
            for mref in msg.media:
                stem = Path(mref.file).stem
                if mref.kind == "audio":
                    tfile = transcripts_dir / (stem + ".txt")
                    if tfile.exists():
                        try:
                            text = tfile.read_text(encoding="utf-8", errors="replace").strip()
                            enr.transcript_file = relpath_posix(tfile, out_dir)
                            enr.transcript_text = text
                        except Exception:
                            pass

                    # prefer mp3 if present
                    mp3 = converted_dir / (stem + ".mp3")
                    wav = converted_dir / (stem + ".wav")
                    if mp3.exists():
                        mref.converted_file = relpath_posix(mp3, out_dir)
                    elif wav.exists():
                        mref.converted_file = relpath_posix(wav, out_dir)

                if mref.kind == "image":
                    ofile = ocr_dir / (stem + ".txt")
                    if ofile.exists():
                        try:
                            text = ofile.read_text(encoding="utf-8", errors="replace").strip()
                            enr.ocr_text_file = relpath_posix(ofile, out_dir)
                            enr.ocr_text = text
                        except Exception:
                            pass

            if enr.ocr_text_file or enr.transcript_file:
                msg.enrichment = enr

            obj = asdict(msg)
            obj["sender_id"] = sender_to_id(msg.sender, me, them)
            f_out.write(json.dumps(obj, ensure_ascii=False) + "\n")

            # Markdown
            try:
                dt = datetime.fromisoformat(msg.ts)
            except Exception:
                dt = None

            month_key = dt.strftime("%Y-%m") if dt else "unknown"
            ts_disp = dt.strftime("%Y-%m-%d %H:%M") if dt else msg.ts

            who = msg.sender or "SYSTEM"
            sid = obj.get("sender_id") or who
            who_disp = f"{sid} ({who})" if sid in {"ME", "THEM"} and who != "SYSTEM" else who

            if md_f is not None:
                if month_key != last_month:
                    md_f.write(f"\n## {month_key}\n\n")
                    last_month = month_key

                if msg.type == "text":
                    md_f.write(f"- **{ts_disp}** — **{who_disp}**: {(msg.text or '').strip()}\n")
                elif msg.type == "system":
                    md_f.write(f"- **{ts_disp}** — *(system)*: {(msg.text or '').strip()}\n")
                else:
                    files = ", ".join([m.get("file") for m in obj.get("media", []) if m.get("file")])
                    md_f.write(f"- **{ts_disp}** — **{who_disp}**: *{msg.type}* ({files})\n")
                    enr_obj = obj.get("enrichment") or {}
                    ttxt = enr_obj.get("transcript_text")
                    if isinstance(ttxt, str) and ttxt.strip():
                        md_f.write(f"  - Transcript: {clip(ttxt, md_max_chars)}\n")
                    ocr = enr_obj.get("ocr_text")
                    if isinstance(ocr, str) and ocr.strip():
                        md_f.write(f"  - OCR: {clip(ocr, md_max_chars)}\n")

            if write_by_month:
                marker_idx = month_marker_index.get(month_key)
                if marker_idx is None or i > marker_idx:
                    jf, mf = get_month_handles(month_key)
                    if jf is not None:
                        jf.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    if mf is not None:
                        if msg.type == "text":
                            mf.write(f"- **{ts_disp}** — **{who_disp}**: {(msg.text or '').strip()}\n")
                        elif msg.type == "system":
                            mf.write(f"- **{ts_disp}** — *(system)*: {(msg.text or '').strip()}\n")
                        else:
                            files = ", ".join([m.get("file") for m in obj.get("media", []) if m.get("file")])
                            mf.write(f"- **{ts_disp}** — **{who_disp}**: *{msg.type}* ({files})\n")
                            enr_obj = obj.get("enrichment") or {}
                            ttxt = enr_obj.get("transcript_text")
                            if isinstance(ttxt, str) and ttxt.strip():
                                mf.write(f"  - Transcript: {clip(ttxt, md_max_chars)}\n")
                            ocr = enr_obj.get("ocr_text")
                            if isinstance(ocr, str) and ocr.strip():
                                mf.write(f"  - OCR: {clip(ocr, md_max_chars)}\n")

    for f in by_month_jsonl.values():
        try:
            f.close()
        except Exception:
            pass
    for f in by_month_md.values():
        try:
            f.close()
        except Exception:
            pass

    if md_f is not None:
        md_f.close()
        manifest.log({"type": "md_written", "output": str(md_path)})

    return jsonl_path, (md_path if write_md else None), (by_month_dir if write_by_month else None)
