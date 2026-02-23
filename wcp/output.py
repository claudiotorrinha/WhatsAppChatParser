from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .manifest import ManifestLogger
from .models import Enrichment, Message
from .util import clip, media_artifact_stems, relpath_posix


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
    transcript_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
    ocr_cache: dict[str, tuple[Optional[str], Optional[str]]] = {}
    converted_cache: dict[str, Optional[str]] = {}

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

    def artifact_candidates(base_dir: Path, file_name: str, suffix: str) -> list[Path]:
        return [base_dir / (stem + suffix) for stem in media_artifact_stems(file_name)]

    def read_text_artifact_cached(
        cache: dict[str, tuple[Optional[str], Optional[str]]],
        *,
        file_name: str,
        base_dir: Path,
    ) -> tuple[Optional[str], Optional[str]]:
        if file_name in cache:
            return cache[file_name]
        out_rel: Optional[str] = None
        out_text: Optional[str] = None
        for path in artifact_candidates(base_dir, file_name, ".txt"):
            if not path.exists():
                continue
            out_rel = relpath_posix(path, out_dir)
            try:
                out_text = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                out_text = None
            break
        cache[file_name] = (out_rel, out_text)
        return out_rel, out_text

    def converted_artifact_cached(file_name: str) -> Optional[str]:
        if file_name in converted_cache:
            return converted_cache[file_name]
        for ext in (".mp3", ".wav"):
            for path in artifact_candidates(converted_dir, file_name, ext):
                if path.exists():
                    converted_cache[file_name] = relpath_posix(path, out_dir)
                    return converted_cache[file_name]
        converted_cache[file_name] = None
        return None

    with jsonl_path.open("w", encoding="utf-8") as f_out:
        for i, msg in enumerate(messages):
            # Enrichment attach
            enr = Enrichment()
            for mref in msg.media:
                if mref.kind == "audio":
                    t_rel, t_text = read_text_artifact_cached(
                        transcript_cache,
                        file_name=mref.file,
                        base_dir=transcripts_dir,
                    )
                    if t_rel:
                        enr.transcript_file = t_rel
                    if isinstance(t_text, str):
                        enr.transcript_text = t_text
                    converted_rel = converted_artifact_cached(mref.file)
                    if converted_rel:
                        mref.converted_file = converted_rel

                if mref.kind == "image":
                    o_rel, o_text = read_text_artifact_cached(
                        ocr_cache,
                        file_name=mref.file,
                        base_dir=ocr_dir,
                    )
                    if o_rel:
                        enr.ocr_text_file = o_rel
                    if isinstance(o_text, str):
                        enr.ocr_text = o_text

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
