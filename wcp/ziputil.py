from __future__ import annotations

import zipfile
from pathlib import Path


def _is_within_directory(base: Path, target: Path) -> bool:
    base = base.resolve()
    target = target.resolve()
    try:
        target.relative_to(base)
        return True
    except Exception:
        return False


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract zip into dest_dir safely (prevents Zip Slip)."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        for info in z.infolist():
            out_path = dest_dir / info.filename
            if not _is_within_directory(dest_dir, out_path):
                raise RuntimeError(f"Unsafe zip entry path: {info.filename}")
        z.extractall(dest_dir)

    return dest_dir


def find_export_root(extracted_dir: Path) -> Path:
    """Try to locate the folder that contains the WhatsApp .txt export file.

    WhatsApp exports often contain a nested directory; this resolves to the directory
    containing the first matching .txt found.
    """
    extracted_dir = extracted_dir.resolve()

    # Prefer local .txts
    txts = list(extracted_dir.glob("*.txt"))
    if txts:
        return extracted_dir

    # Search recursively for txt
    candidates = list(extracted_dir.rglob("*.txt"))
    if not candidates:
        return extracted_dir

    def score(p: Path) -> int:
        name = p.name.lower()
        s = 0
        if "whatsapp" in name:
            s += 10
        if "chat" in name or "conversa" in name:
            s += 5
        return s

    best = sorted(candidates, key=lambda p: (score(p), p.stat().st_size if p.exists() else 0), reverse=True)[0]
    return best.parent
