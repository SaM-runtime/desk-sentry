"""Small, privacy-preserving alert counters stored by local calendar day."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

HOURS_PER_DAY = 24
RETENTION_DAYS = 30
SUMMARY_DAYS = 7


@dataclass(frozen=True, slots=True)
class StatsSummary:
    today: int
    last_seven: int
    hours: tuple[int, ...]
    current_hour: int
    has_data: bool


class AlertStats:
    """Persist hourly alert-start counts without frames or detection details."""

    def __init__(
        self,
        path: Path,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = path
        self._clock = clock

    def load(self) -> dict[str, tuple[int, ...]]:
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}

        validated: dict[str, tuple[int, ...]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not _valid_date_key(key):
                return {}
            if not isinstance(value, list) or len(value) != HOURS_PER_DAY:
                return {}
            if any(type(count) is not int or count < 0 for count in value):
                return {}
            validated[key] = tuple(value)
        return validated

    def save(self, data: Mapping[str, Sequence[int]]) -> None:
        payload = _validate_for_save(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def record(
        self,
        *,
        alert_started: bool,
        continued: bool = False,
        now: datetime | None = None,
    ) -> bool:
        if not alert_started or continued:
            return False
        moment = now or self._clock()
        data = self.load()
        day_key = moment.date().isoformat()
        hours = list(data.get(day_key, (0,) * HOURS_PER_DAY))
        hours[moment.hour] += 1
        data[day_key] = tuple(hours)
        self.save(_pruned(data, moment.date()))
        return True

    def prune(self, now: datetime | None = None) -> bool:
        """Remove dates outside the rolling retention window, if needed."""

        moment = now or self._clock()
        data = self.load()
        if not data:
            return False
        retained = _pruned(data, moment.date())
        if retained == data:
            return False
        self.save(retained)
        return True

    def summary(self, now: datetime | None = None) -> StatsSummary:
        moment = now or self._clock()
        data = self.load()
        today_key = moment.date().isoformat()
        hours = data.get(today_key, (0,) * HOURS_PER_DAY)
        first_day = moment.date() - timedelta(days=SUMMARY_DAYS - 1)
        recent_total = sum(
            sum(counts)
            for key, counts in data.items()
            if first_day <= date.fromisoformat(key) <= moment.date()
        )
        return StatsSummary(
            today=sum(hours),
            last_seven=recent_total,
            hours=hours,
            current_hour=moment.hour,
            has_data=any(any(counts) for counts in data.values()),
        )


def _valid_date_key(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validate_for_save(data: Mapping[str, Sequence[int]]) -> dict[str, list[int]]:
    payload: dict[str, list[int]] = {}
    for key, value in data.items():
        counts = list(value)
        if not _valid_date_key(key) or len(counts) != HOURS_PER_DAY:
            raise ValueError("alert stats must contain YYYY-MM-DD keys with 24 hourly counts")
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("hourly alert counts must be nonnegative integers")
        payload[key] = counts
    return payload


def _pruned(
    data: Mapping[str, tuple[int, ...]],
    today: date,
) -> dict[str, tuple[int, ...]]:
    oldest = today - timedelta(days=RETENTION_DAYS - 1)
    return {
        key: counts for key, counts in data.items() if oldest <= date.fromisoformat(key) <= today
    }
