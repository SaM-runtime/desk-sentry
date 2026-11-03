from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from desk_sentry.stats import AlertStats

NOW = datetime(2026, 10, 12, 14, 30)


def test_stats_counts_started_alerts_by_hour_and_skips_continued(tmp_path: Path) -> None:
    stats = AlertStats(tmp_path / "alert-stats.json", clock=lambda: NOW)

    assert stats.record(alert_started=True)
    assert not stats.record(alert_started=False)
    assert not stats.record(alert_started=True, continued=True)

    summary = stats.summary()
    assert summary.today == 1
    assert summary.last_seven == 1
    assert summary.hours[14] == 1
    assert summary.current_hour == 14
    assert summary.has_data


def test_stats_prunes_to_thirty_calendar_days(tmp_path: Path) -> None:
    path = tmp_path / "alert-stats.json"
    path.write_text(
        json.dumps(
            {
                "2026-09-12": [1] + [0] * 23,
                "2026-09-13": [2] + [0] * 23,
                "2026-10-12": [0] * 24,
            }
        ),
        encoding="utf-8",
    )

    stats = AlertStats(path, clock=lambda: NOW)
    assert stats.record(alert_started=True)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "2026-09-12" not in saved
    assert saved["2026-09-13"][0] == 2
    assert saved["2026-10-12"][14] == 1


def test_stats_prune_removes_expired_day_without_recording_an_alert(tmp_path: Path) -> None:
    path = tmp_path / "alert-stats.json"
    path.write_text(
        json.dumps(
            {
                "2026-09-12": [1] + [0] * 23,
                "2026-09-13": [2] + [0] * 23,
                "2026-10-12": [3] + [0] * 23,
            }
        ),
        encoding="utf-8",
    )

    assert AlertStats(path, clock=lambda: NOW).prune()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"2026-09-13", "2026-10-12"}


def test_stats_prune_does_not_create_or_rewrite_when_nothing_expired(tmp_path: Path) -> None:
    path = tmp_path / "alert-stats.json"
    stats = AlertStats(path, clock=lambda: NOW)
    assert not stats.prune()
    assert not path.exists()

    path.write_text(json.dumps({"2026-09-13": [1] + [0] * 23}), encoding="utf-8")
    preserved_timestamp = 1_000_000_000
    os.utime(path, ns=(preserved_timestamp, preserved_timestamp))
    assert not stats.prune()
    assert path.stat().st_mtime_ns == preserved_timestamp


def test_stats_summary_uses_seven_days_including_today(tmp_path: Path) -> None:
    path = tmp_path / "alert-stats.json"
    path.write_text(
        json.dumps(
            {
                "2026-10-05": [9] + [0] * 23,
                "2026-10-06": [2] + [0] * 23,
                "2026-10-11": [3] + [0] * 23,
                "2026-10-12": [0] * 14 + [4] + [0] * 9,
            }
        ),
        encoding="utf-8",
    )

    summary = AlertStats(path, clock=lambda: NOW).summary()
    assert summary.today == 4
    assert summary.last_seven == 9
    assert summary.hours[14] == 4


def test_stats_rejects_entire_malformed_file_instead_of_partially_loading(
    tmp_path: Path,
) -> None:
    path = tmp_path / "alert-stats.json"
    invalid_payloads = (
        "not json",
        json.dumps({"2026-10-12": [0] * 23}),
        json.dumps({"2026-10-12": [0] * 24, "bad-date": [0] * 24}),
        json.dumps({"2026-10-12": [0] * 23 + [-1]}),
        json.dumps({"2026-10-12": [0] * 23 + [True]}),
    )
    stats = AlertStats(path, clock=lambda: NOW)
    for payload in invalid_payloads:
        path.write_text(payload, encoding="utf-8")
        assert stats.load() == {}


def test_stats_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "alert-stats.json"
    stats = AlertStats(path, clock=lambda: NOW)

    assert stats.record(alert_started=True)

    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(path.read_text(encoding="utf-8"))["2026-10-12"][14] == 1
