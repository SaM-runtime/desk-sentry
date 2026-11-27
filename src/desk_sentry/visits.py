"""離座紀錄: photos of people who came to the desk while the user was away.

Unlike alert thumbnails, these photos are only useful after the fact (the user
comes back and wants to know who was at their computer), so they survive a
restart. Each photo is stored as one file whose whole content (time stamp,
"sat in your seat" flag and JPEG) is encrypted with Windows DPAPI for the
current account. Photos expire after ``RETENTION_DAYS`` and at most
``MAX_VISITS`` are kept. Without DPAPI (or without a directory) the log stays
in memory only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

RETENTION_DAYS = 7
MAX_VISITS = 60
_MAGIC = b"DSV1"
_SUFFIX = ".dsv"
_JPEG_QUALITY = 85

Codec = Callable[[bytes], bytes]
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Visit:
    key: str
    taken_at: float
    seated: bool
    image: NDArray[np.uint8]  # RGB


class VisitLog:
    def __init__(
        self,
        directory: Path | None,
        *,
        protect: Codec | None,
        unprotect: Codec | None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        persistent = directory is not None and protect is not None and unprotect is not None
        self._directory = directory if persistent else None
        self._protect = protect
        self._unprotect = unprotect
        self._clock = clock
        self._items: dict[str, Visit] = {}
        self._session = f"{int(clock() * 1000):x}"
        self.on_change: Callable[[], None] | None = None

    @property
    def persistent(self) -> bool:
        return self._directory is not None

    # ------------------------------------------------------------- reading
    def load(self) -> None:
        """Read stored photos, dropping expired ones; unreadable files are skipped."""

        directory = self._directory
        if directory is None or not directory.is_dir():
            return
        cutoff = self._clock() - RETENTION_DAYS * 86400
        for path in sorted(directory.glob(f"*{_SUFFIX}")):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    continue
                visit = self._decode(path.stem, path.read_bytes())
            except (OSError, ValueError) as error:
                # Another Windows account's file, or a damaged one: never fatal.
                _log.info("Skipping unreadable visit photo: %s", type(error).__name__)
                continue
            if visit.taken_at < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
                continue
            self._items[visit.key] = visit
        self._trim()

    def items(self) -> list[Visit]:
        """Newest first."""

        return sorted(self._items.values(), key=lambda v: v.taken_at, reverse=True)

    def unseen(self, seen_at: float) -> int:
        return sum(1 for v in self._items.values() if v.taken_at > seen_at)

    def __len__(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------- writing
    def add(self, visit_id: int, image: NDArray[np.uint8], *, seated: bool, taken_at: float) -> str:
        """Add a photo; a later photo of the same visit replaces the earlier one."""

        key = f"{self._session}-{visit_id}"
        previous = self._items.get(key)
        visit = Visit(
            key,
            previous.taken_at if previous is not None else taken_at,
            seated or (previous.seated if previous is not None else False),
            np.ascontiguousarray(image, dtype=np.uint8),
        )
        self._items[key] = visit
        self._write(visit)
        self._trim()
        self._changed()
        return key

    def clear(self) -> None:
        keys = list(self._items)
        self._items.clear()
        for key in keys:
            self._delete_file(key)
        directory = self._directory
        if directory is not None and directory.is_dir():
            for path in directory.glob(f"*{_SUFFIX}"):
                with contextlib.suppress(OSError):
                    path.unlink()
        self._changed()

    def expire(self) -> None:
        cutoff = self._clock() - RETENTION_DAYS * 86400
        expired = [key for key, visit in self._items.items() if visit.taken_at < cutoff]
        for key in expired:
            del self._items[key]
            self._delete_file(key)
        if expired:
            self._changed()

    # ------------------------------------------------------------- helpers
    def _trim(self) -> None:
        for visit in self.items()[MAX_VISITS:]:
            del self._items[visit.key]
            self._delete_file(visit.key)

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _path(self, key: str) -> Path | None:
        if self._directory is None:
            return None
        return self._directory / f"{key}{_SUFFIX}"

    def _delete_file(self, key: str) -> None:
        path = self._path(key)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    def _write(self, visit: Visit) -> None:
        path = self._path(visit.key)
        protect = self._protect
        if path is None or protect is None:
            return
        ok, jpeg = cv2.imencode(
            ".jpg",
            cv2.cvtColor(visit.image, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
        )
        if not ok:
            return
        header = json.dumps({"taken_at": visit.taken_at, "seated": visit.seated}).encode()
        plain = struct.pack("<I", len(header)) + header + jpeg.tobytes()
        try:
            payload = _MAGIC + protect(plain)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        except OSError as error:
            # Keep the photo in memory; the user still sees it this session.
            _log.warning("Cannot save visit photo: %s", type(error).__name__)

    def _decode(self, key: str, payload: bytes) -> Visit:
        unprotect = self._unprotect
        if unprotect is None or not payload.startswith(_MAGIC):
            raise ValueError("not a visit photo")
        plain = unprotect(payload[len(_MAGIC) :])
        if len(plain) < 4:
            raise ValueError("truncated visit photo")
        (size,) = struct.unpack("<I", plain[:4])
        header = json.loads(plain[4 : 4 + size].decode())
        if not isinstance(header, dict):
            raise ValueError("bad visit header")
        taken_at = float(header.get("taken_at", 0.0))
        seated = bool(header.get("seated", False))
        jpeg = np.frombuffer(plain[4 + size :], dtype=np.uint8)
        decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("bad visit image")
        image = np.ascontiguousarray(cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB), dtype=np.uint8)
        return Visit(key, taken_at, seated, image)
