from __future__ import annotations

from pathlib import Path


def should_ocr_image(img_path: Path, mode: str, edge_threshold: float = 18.0, downscale: int = 512) -> bool:
    """Heuristic gate for OCR to speed up runs.

    mode:
      - "all": always OCR
      - "likely-text": OCR only if the image looks text-heavy (screenshots)

    Uses a downscaled edge-density estimate.
    """
    if mode == "all":
        return True
    if mode != "likely-text":
        return True

    try:
        from PIL import Image, ImageFilter  # type: ignore
    except Exception:
        # If pillow isn't installed, OCR may fail anyway; let caller handle.
        return True

    try:
        img = Image.open(img_path)
        img = img.convert("L")
        w, h = img.size
        if max(w, h) > downscale:
            scale = downscale / float(max(w, h))
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))

        edges = img.filter(ImageFilter.FIND_EDGES)
        hist = edges.histogram()
        total = sum(hist) or 1
        mean = sum(i * c for i, c in enumerate(hist)) / total
        return mean >= edge_threshold
    except Exception:
        return True
