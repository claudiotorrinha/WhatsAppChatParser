from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .util import now_utc_iso


class ManifestLogger:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self._f: Optional[object] = None
        self._lock = threading.Lock()

    def open(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            finally:
                self._f = None

    def log(self, event: dict) -> None:
        if not self.enabled or self._f is None:
            return
        event.setdefault("event_ts", now_utc_iso())
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._f.write(line + "\n")
            self._f.flush()
