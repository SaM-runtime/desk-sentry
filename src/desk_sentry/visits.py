"""離座紀錄: photos of people who came to the desk while the user was away.

By default the photos live in memory only and disappear when the app exits.
Saving them is a separate opt-in (``configure(save=True)``): each photo is then
stored as one file whose whole content (time stamp, "sat in your seat" flag and
JPEG) is encrypted with Windows DPAPI for the current account. Photos expire
after the chosen number of days and at most ``MAX_VISITS`` are kept. Without
DPAPI (or without a directory) saving is unavailable.
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

RETENTION_DAYS = 7  # default; the user can choose one of RETENTION_CHOICES
RETENTION_CHOICES = (1, 3, 7, 30)
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
        capable = directory is not None and protect is not None and unprotect is not None
        self._store = directory if capable else None
        self._save = False
        self.retention_days = RETENTION_DAYS
        self._protect = protect
        self._unprotect = unprotect
        self._clock = clock
        self._items: dict[str, Visit] = {}
        self._session = f"{int(clock() * 1000):x}"
        self.on_change: Callable[[], None] | None = None

    @property
    def can_save(self) -> bool:
        """Encrypted saving is possible here (Windows DPAPI and a data folder)."""

        return self._store is not None

    @property
    def persistent(self) -> bool:
        """Photos are currently being written to disk."""

        return self._save and self._store is not None

    @property
    def _directory(self) -> Path | None:
        return self._store if self._save else None

    def configure(self, *, save: bool, retention_days: int) -> None:
        """Apply the user's choices.

        Turning saving on writes the photos already taken this session; turning
        it off deletes every saved file (the photos stay viewable until exit).
        """

        days = retention_days if retention_days in RETENTION_CHOICES else RETENTION_DAYS
        retention_changed = days != self.retention_days
        self.retention_days = days
        was_saving = self.persistent
        self._save = bool(save)
        if self.persistent and not was_saving:
            for visit in self._items.values():
                self._write(visit)
        elif was_saving and not self.persistent:
            self._delete_all_files()
        if retention_changed:
            self.expire()

    def _cutoff(self) -> float:
        return self._clock() - self.retention_days * 86400

    # ------------------------------------------------------------- reading
    def load(self) -> None:
        """Read stored photos, dropping expired ones; unreadable files are skipped."""

        directory = self._directory
        if directory is None or not directory.is_dir():
            return
        cutoff = self._cutoff()
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
        self._items.clear()
        self._delete_all_files()
        self._changed()

    def _delete_all_files(self) -> None:
        directory = self._store
        if directory is not None and directory.is_dir():
            for path in directory.glob(f"*{_SUFFIX}"):
                with contextlib.suppress(OSError):
                    path.unlink()

    def expire(self) -> None:
        cutoff = self._cutoff()
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
