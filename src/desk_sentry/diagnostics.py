"""Opt-in diagnostics: numbers only (boxes, scores, verdicts, timings), never pixels.

One JSON object per line, gzip-compressed per day in ``<dir>/YYYY-MM-DD.jsonl.gz``.
Analysis records are rate-limited by what is in view (see ``RATE_SECONDS``) and
buffered in memory, then appended as one gzip member per batch (~9x smaller
than plain text: a busy "high"-sensitivity day is ~3-4 MB instead of 30-40 MB).
At most ``FLUSH_SECONDS`` of records can be lost if the process is killed.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 10 * 1024 * 1024  # compressed
MAX_FOLDER_BYTES = 100 * 1024 * 1024
FLUSH_LINES = 200
FLUSH_SECONDS = 60.0
_PATTERNS = ("*.jsonl", "*.jsonl.gz")
KEEP_DAYS = 14
# Minimum spacing between analysis records per level; alert starts and status
# changes are always written.
RATE_SECONDS = {"full": 0.0, "people": 0.5, "quiet": 1.0}


class DiagnosticsLog:
    def __init__(self, folder: Path, *, clock: Any = time.time) -> None:
        self.folder = folder
        self._clock = clock
        self._lock = threading.Lock()
        self._last_written = float("-inf")
        self._last_status: str | None = None
        self._buffer: list[str] = []
        self._buffer_day: str | None = None
        self._last_flush = self._clock()

    def _day(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    def write(self, kind: str, **fields: Any) -> None:
        now = self._clock()
        record = {"ts": round(now, 3), "kind": kind, **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            day = self._day(now)
            if self._buffer_day is not None and day != self._buffer_day:
                self._flush_locked()  # keep each record in its own day file
            self._buffer_day = day
            self._buffer.append(line)
            if len(self._buffer) >= FLUSH_LINES or now - self._last_flush >= FLUSH_SECONDS:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        lines, self._buffer = self._buffer, []
        self._last_flush = self._clock()
        if not lines or self._buffer_day is None:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / f"{self._buffer_day}.jsonl.gz"
            if not path.exists():
                self._prune(keep=path)
            if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
                return
            with path.open("ab") as handle:  # concatenated gzip members are valid gzip
                handle.write(gzip.compress("".join(lines).encode("utf-8"), compresslevel=6))
        except OSError:
            pass  # diagnostics must never break the sentry

    def _prune(self, keep: Path) -> None:
        """Drop day files older than KEEP_DAYS, then oldest first above MAX_FOLDER_BYTES."""

        cutoff = self._clock() - KEEP_DAYS * 86400
        files = []
        day_files = sorted(
            (f for pattern in _PATTERNS for f in self.folder.glob(pattern)), key=lambda f: f.name
        )
        for f in day_files:
            try:
                stat = f.stat()
                if stat.st_mtime < cutoff:
                    f.unlink()
                elif f != keep:
                    files.append((f, stat.st_size))
            except OSError:
                continue
        # Leave room for today's file at its cap.
        budget = MAX_FOLDER_BYTES - MAX_FILE_BYTES
        total = sum(size for _, size in files)
        for f, size in files:  # oldest names sort first
            if total <= budget:
                break
            try:
                f.unlink()
                total -= size
            except OSError:
                continue

    def analysis(
        self,
        *,
        status: str,
        level: str,
        alert_started: bool,
        continued: bool = False,
        **fields: Any,
    ) -> None:
        now = self._clock()
        changed = status != self._last_status
        self._last_status = status
        due = now - self._last_written >= RATE_SECONDS.get(level, 1.0)
        if not (due or alert_started or continued or changed):
            return
        self._last_written = now
        self.write(
            "analysis",
            status=status,
            alert_started=alert_started,
            continued=continued,
            level=level,
            **fields,
        )
