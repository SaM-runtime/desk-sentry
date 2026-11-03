"""Review regressions at the application's queued snapshot and shortcut boundaries."""

import gzip
import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import Mock

import pytest

from desk_sentry import actions
from desk_sentry.config import Settings
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Snapshot
from desk_sentry.people import Level
from desk_sentry.sentry import Status
from desk_sentry.stats import AlertStats
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow
from desk_sentry.ui.mini_mirror import MiniMirror
from test_app_hide04 import app_with


@pytest.mark.parametrize("error_type", [OSError, PermissionError, RuntimeError])
def test_stats_failure_keeps_alert_action_and_mirror_updates(tmp_path, monkeypatch, error_type):
    mono = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: mono[0])
    now = datetime(2026, 10, 12, 14)
    app = app_with(
        Settings(
            action="minimize_app",
            target_apps=("mail.exe",),
            restore_after_alert=True,
            diagnostics=True,
            edge_flash=False,
            sound=False,
        )
    )
    app._wall_now = lambda: now
    app._stats_day = now.date()
    app._stats_hour = now.hour
    app._history_window = None
    app._diagnostics = DiagnosticsLog(tmp_path / "log")

    class FailingStats(AlertStats):
        def record(self, **kwargs):
            raise error_type("injected stats storage failure")

    app.stats = FailingStats(tmp_path / "stats.json")
    minimize = Mock(return_value={101: False})
    monkeypatch.setattr(actions, "minimize_apps", minimize)
    snapshot = Snapshot(Status.ALERT, alert_level=Level.HIGH, evade_near=True)
    for started_at in (0.0, 31.0):
        mono[0] = started_at
        app._on_snapshot(replace(snapshot, alert_started=True))
        mono[0] = started_at + 0.4
        app._on_snapshot(snapshot)

    assert minimize.call_count == 2
    minimize.assert_called_with(("mail.exe",))
    assert app._restore_plan.pending == {101: False}
    assert app.window.show_snapshot.call_count == 4
    assert app.popup.update_snapshot.call_count == 4
    assert app._latest_snapshot is snapshot
    app._diagnostics.flush()
    records = [
        json.loads(line)
        for path in (tmp_path / "log").glob("*.jsonl.gz")
        for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["kind"] == "stats_error"
    assert "injected stats storage failure" in records[0]["error"]


@pytest.mark.parametrize(
    "armed,effective,status",
    [(True, False, Status.CLEAR), (False, False, Status.CLEAR), (True, True, Status.PAUSED)],
    ids=["automatic-pause", "manual-pause", "rearmed-stale-paused"],
)
def test_stale_snapshot_cannot_restore_or_record_stats(
    tmp_path, monkeypatch, armed, effective, status
):
    app = app_with(Settings(armed=armed, action="minimize_app", restore_after_alert=True))
    app._effective_armed = effective
    app._restore_plan.record({101: True})
    app._restore_plan.update(Status.CLEAR, 0.0)
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: 5.0)
    restore = Mock()
    monkeypatch.setattr(actions, "restore_windows", restore)
    now = datetime(2026, 10, 12, 14)
    app._wall_now = lambda: now
    app._stats_day = now.date()
    app._stats_hour = now.hour
    app._history_window = None
    app.stats = AlertStats(tmp_path / "stats.json")
    app._stats_day = datetime(2026, 10, 11).date()
    app.stats.save({"2026-08-01": (1,) + (0,) * 23})
    original_stats = app.stats.path.read_bytes()
    previous = Snapshot(Status.WATCHING)
    app._latest_snapshot = previous

    app._on_snapshot(Snapshot(status, raw_status=Status.CLEAR))

    restore.assert_not_called()
    assert app._restore_plan.pending == {101: True}
    assert app._latest_snapshot is previous
    app.window.show_snapshot.assert_not_called()
    app.popup.update_snapshot.assert_not_called()
    # Even cross-day pruning must not write while this snapshot is being discarded.
    assert app.stats.path.read_bytes() == original_stats


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("was_visible", [False, True])
def test_curtain_restores_mirror_visibility_before_curtain(
    qtbot, monkeypatch, compact, was_visible
):
    monkeypatch.setattr(_CurtainWindow, "showFullScreen", lambda window: window.show())
    monkeypatch.setattr("desk_sentry.ui.curtain.motion.animations_enabled", lambda: False)
    app = app_with(Settings(compact_mode=compact, hide_hotkey_style="curtain"))
    app.mini = MiniMirror()
    qtbot.addWidget(app.mini)
    app.mini.show()
    if not was_visible:
        app.toggle_mini()  # the user hid it with the mirror shortcut
    assert app.mini.isVisible() is was_visible
    app._latest_snapshot = Snapshot(Status.PAUSED)
    app.curtain = CurtainGroup(mirror=app.mini, on_hidden=app._curtain_hidden)

    app.toggle_hidden_windows()
    for window in app.curtain.windows:
        qtbot.addWidget(window)
    assert app.curtain.visible
    assert app.mini.isVisible()
    app.toggle_hidden_windows()

    assert not app.curtain.visible
    assert app.mini.isVisible() is was_visible
    assert app.window.settings.compact_mode is compact
