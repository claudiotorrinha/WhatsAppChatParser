from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def relpath_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


_MEDIA_ARTIFACT_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_media_ref(file_name: str) -> str:
    # Normalize to a stable POSIX-like path key.
    normalized = str(file_name or "").replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def media_artifact_stem(file_name: str) -> str:
    normalized = normalize_media_ref(file_name)
    base = Path(normalized).stem or "media"
    safe_base = _MEDIA_ARTIFACT_SAFE_RE.sub("_", base).strip("._-") or "media"
    digest = hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{safe_base}__{digest}"


def legacy_media_artifact_stem(file_name: str) -> str:
    return Path(file_name).stem


def media_artifact_stems(file_name: str) -> list[str]:
    primary = media_artifact_stem(file_name)
    legacy = legacy_media_artifact_stem(file_name)
    if primary == legacy:
        return [primary]
    return [primary, legacy]


def fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ffprobe_duration_seconds(path: Path) -> Optional[float]:
    # Requires ffprobe (ships with ffmpeg)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace").strip()
        if not out:
            return None
        return float(out)
    except Exception:
        return None


def image_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None, None

    try:
        with Image.open(path) as img:
            w, h = img.size
            return int(w), int(h)
    except Exception:
        return None, None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
