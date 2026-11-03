"""People the user has labelled, each with an alert level ("警戒程度").

Only face feature vectors (128 numbers per sample) and the user's own labels are
kept, never photos. On disk the whole book is encrypted with Windows DPAPI for
the current user; elsewhere it is memory-only.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from desk_sentry import dpapi

Vector = NDArray[np.float32]

# SFace cosine similarity: same person ~0.6-0.95, different people mostly < 0.3.
# OpenCV's reference threshold is 0.363; stricter here, plus a margin over the
# runner-up so two similar-looking people are reported as unknown, not swapped.
MATCH_THRESHOLD = 0.42
MATCH_MARGIN = 0.06
MAX_SAMPLES = 12
MAX_PEOPLE = 50
_MAGIC = b"DSP1"


class Level(StrEnum):
    HIGH = "high"  # full alert: flash, sound, action
    MEDIUM = "medium"  # mirror turns red only
    LOW = "low"  # no alert, just show the name


LEVEL_TEXT = {
    Level.HIGH: "高：完整提醒",
    Level.MEDIUM: "中：只變紅框",
    Level.LOW: "低：不提醒",
}


@dataclass(slots=True)
class Person:
    pid: str
    name: str
    level: Level
    samples: list[Vector] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Match:
    pid: str
    name: str
    level: Level
    similarity: float


@dataclass(frozen=True, slots=True)
class PeopleSnapshot:
    """Immutable copy handed to the engine thread."""

    pids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    levels: tuple[Level, ...] = ()
    owners: NDArray[np.int32] = field(default_factory=lambda: np.zeros(0, np.int32))
    matrix: NDArray[np.float32] = field(default_factory=lambda: np.zeros((0, 128), np.float32))

    @property
    def empty(self) -> bool:
        return not self.pids

    @property
    def has_quiet_people(self) -> bool:
        return any(level is not Level.HIGH for level in self.levels)

    def match(self, embedding: Vector) -> Match | None:
        if self.empty or self.matrix.shape[0] == 0:
            return None
        sims = self.matrix @ embedding
        best_per_person = np.full(len(self.pids), -1.0, np.float32)
        np.maximum.at(best_per_person, self.owners, sims)
        order = np.argsort(best_per_person)[::-1]
        best = int(order[0])
        top = float(best_per_person[best])
        runner_up = float(best_per_person[order[1]]) if len(order) > 1 else -1.0
        if top < MATCH_THRESHOLD or top - runner_up < MATCH_MARGIN:
            return None
        return Match(self.pids[best], self.names[best], self.levels[best], top)


def normalize(vector: object) -> Vector | None:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    if v.shape != (128,) or not np.isfinite(norm) or norm <= 1e-6:
        return None
    return (v / norm).astype(np.float32)


class PeopleBook:
    def __init__(
        self,
        path: Path | None,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.path = path
        self._protect = protect or (dpapi.protect if dpapi.available() else None)
        self._unprotect = unprotect or (dpapi.unprotect if dpapi.available() else None)
        self.people: list[Person] = []
        self.load_error = False

    # ---------------------------------------------------------------- queries
    def get(self, pid: str) -> Person | None:
        return next((p for p in self.people if p.pid == pid), None)

    def find_name(self, name: str) -> Person | None:
        key = name.strip().casefold()
        return next((p for p in self.people if p.name.casefold() == key), None)

    def snapshot(self) -> PeopleSnapshot:
        rows: list[Vector] = []
        owners: list[int] = []
        for index, person in enumerate(self.people):
            rows.extend(person.samples)
            owners.extend([index] * len(person.samples))
        return PeopleSnapshot(
            tuple(p.pid for p in self.people),
            tuple(p.name for p in self.people),
            tuple(p.level for p in self.people),
            np.asarray(owners, np.int32),
            np.vstack(rows).astype(np.float32) if rows else np.zeros((0, 128), np.float32),
        )

    # ---------------------------------------------------------------- changes
    def label(self, name: str, level: Level, embedding: Vector) -> Person:
        """Add a face sample under ``name`` (creating the person if needed)."""

        name = name.strip()[:40]
        if not name:
            raise ValueError("name is required")
        vector = normalize(embedding)
        if vector is None:
            raise ValueError("invalid face feature")
        person = self.find_name(name)
        if person is None:
            if len(self.people) >= MAX_PEOPLE:
                raise ValueError("too many people")
            person = Person(secrets.token_hex(4), name, level)
            self.people.append(person)
        person.level = level
        person.samples.append(vector)
        del person.samples[:-MAX_SAMPLES]  # keep the most recent samples
        return person

    def set_level(self, pid: str, level: Level) -> None:
        person = self.get(pid)
        if person is not None:
            person.level = level

    def rename(self, pid: str, name: str) -> None:
        person = self.get(pid)
        name = name.strip()[:40]
        if person is not None and name:
            person.name = name

    def remove(self, pid: str) -> None:
        self.people = [p for p in self.people if p.pid != pid]

    def clear(self) -> None:
        self.people = []

    # ---------------------------------------------------------------- storage
    def _encode(self) -> bytes:
        payload = {
            "version": 1,
            "people": [
                {
                    "pid": p.pid,
                    "name": p.name,
                    "level": p.level.value,
                    "samples": [np.round(s, 5).tolist() for s in p.samples],
                }
                for p in self.people
            ],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _decode(self, data: bytes) -> list[Person]:
        payload = json.loads(data.decode("utf-8"))
        people = []
        for raw in payload.get("people", [])[:MAX_PEOPLE]:
            samples = [v for v in (normalize(s) for s in raw.get("samples", [])) if v is not None]
            try:
                level = Level(raw.get("level", "high"))
            except ValueError:
                level = Level.HIGH
            name = str(raw.get("name", "")).strip()[:40]
            if name and samples:
                people.append(
                    Person(
                        str(raw.get("pid") or secrets.token_hex(4)),
                        name,
                        level,
                        samples[-MAX_SAMPLES:],
                    )
                )
        return people

    @property
    def persistent(self) -> bool:
        return self.path is not None and self._protect is not None

    def load(self) -> None:
        self.load_error = False
        if self.path is None or self._unprotect is None or not self.path.is_file():
            return
        try:
            blob = self.path.read_bytes()
            if not blob.startswith(_MAGIC):
                raise ValueError("unknown format")
            self.people = self._decode(self._unprotect(blob[len(_MAGIC) :]))
        except (OSError, ValueError, KeyError, TypeError):
            # Wrong Windows user, corruption...: start empty and keep the file
            # until the user saves something new.
            self.people = []
            self.load_error = True

    def save(self) -> None:
        if not self.persistent:
            return
        assert self.path is not None and self._protect is not None
        if not self.people:
            if self.path.exists():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(_MAGIC + self._protect(self._encode()))
        os.replace(tmp, self.path)
