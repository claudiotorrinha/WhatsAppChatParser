from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .util import now_utc_iso


class ManifestLogger:
    def __init__(
        self,
        path: Path,
        enabled: bool = True,
        *,
        flush_every: int = 25,
        flush_interval_seconds: float = 1.0,
    ):
        self.path = path
        self.enabled = enabled
        self.flush_every = max(1, int(flush_every))
        self.flush_interval_seconds = max(0.0, float(flush_interval_seconds))
        self._f: Optional[object] = None
        self._lock = threading.Lock()
        self._pending_since_flush = 0
        self._last_flush_ts = 0.0

    def open(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")
        self._pending_since_flush = 0
        self._last_flush_ts = time.monotonic()

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                try:
                    if self._pending_since_flush:
                        self._f.flush()
                    self._f.close()
                finally:
                    self._f = None
                    self._pending_since_flush = 0

    def log(self, event: dict) -> None:
        if not self.enabled or self._f is None:
            return
        event.setdefault("event_ts", now_utc_iso())
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._f.write(line + "\n")
            self._pending_since_flush += 1
            now = time.monotonic()
            if self._pending_since_flush >= self.flush_every or (
                self.flush_interval_seconds and (now - self._last_flush_ts) >= self.flush_interval_seconds
            ):
                self._f.flush()
                self._pending_since_flush = 0
                self._last_flush_ts = now
