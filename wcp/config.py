from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_config_from_argv(argv: list[str]) -> dict[str, Any]:
    """Load JSON config if --config is present in argv."""
    path = None
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            path = argv[i + 1]
            break
        if a.startswith("--config="):
            path = a.split("=", 1)[1]
            break

    if not path:
        return {}

    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"Config not found: {p}")

    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"Failed to parse config JSON: {p}: {e}")
