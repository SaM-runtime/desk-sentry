from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from desk_sentry import actions
from desk_sentry.app import DeskSentryApp, _ObservedEvadePlan
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.health import HealthState
from desk_sentry.people import Level
from desk_sentry.sentry import Status


def app_with(settings):
    app = DeskSentryApp.__new__(DeskSentryApp)
    app.window = Mock(settings=settings)
    app.mini = Mock()
    app.mini.isVisible.return_value = False
    app.popup = Mock()
    app.engine = SimpleNamespace(preview_wanted=False)
    app.history = Mock()
    app.flash = Mock()
    app._last_status = None
    app._build_info = SimpleNamespace(display_version="test", version="test")
    app._available_update = None
    app._meeting_active = False
    app._manual_yield = False
    app._camera_mode = settings.camera_mode
    app._camera_backend = "pending" if settings.camera_mode == "auto" else "exclusive"
    app._camera_backend_detail = ""
    app._camera_subtype = ""
    app._camera_width = 0
    app._camera_height = 0
    app._camera_fps = 0.0
    app._camera_nominal_fps = 0.0
    app._last_camera_error = None
    app._camera_unresponsive = False
    app._camera_release_failed = False
    app._pause_reason = None
    app._pause_ack_reason = None
    app._pause_request = 0
    app._effective_armed = settings.armed
    app._health_state = HealthState.OK
    app._last_alert_ts = None
    app._escalated = False
    app._wall_now = datetime.now
    app._evade_plan = _ObservedEvadePlan()
    app._foreground_plan = actions.ForegroundPlan()
    app._away_plan = actions.AwayPlan()
    app._restore_plan = actions.RestorePlan()
    app.stats = Mock()
    app.stats.record.return_value = False
    now = app._wall_now()
    app._stats_day = now.date()
    app._stats_hour = now.hour
    app._stats_error_logged = False
    app._history_window = None
    app._latest_snapshot = Snapshot(Status.PAUSED)
    app._manual_cover_previous = None
    app._hide_toggle = actions.HideToggle()
    app.curtain = SimpleNamespace(visible=False)
    app._render_tray = Mock()
    return app


def snap(status, **kwargs):
    return Snapshot(status=status, **kwargs)


def test_alert_action_and_raw_status_restoration(monkeypatch):
    now = 0.0
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now)
    minimize = Mock(return_value={101: True, 102: False})
    restore = Mock(return_value=2)
    monkeypatch.setattr(actions, "minimize_apps", minimize)
    monkeypatch.setattr(actions, "restore_windows", restore)
    app = app_with(
        Settings(
            action="minimize_app",
            target_apps=("a.exe",),
            restore_after_alert=True,
            edge_flash=False,
            sound=False,
        )
    )
    app._on_snapshot(
        snap(Status.ALERT, alert_started=True, alert_level=Level.HIGH, evade_near=True)
    )
    now = 0.4
    app._on_snapshot(snap(Status.ALERT, alert_level=Level.HIGH, evade_near=True))
    minimize.assert_called_once_with(("a.exe",))
    now = 1.0
    app._on_snapshot(snap(Status.APPROACHING, raw_status=Status.CLEAR))
    now = 5.9
    app._on_snapshot(snap(Status.WATCHING, raw_status=Status.CLEAR))
    restore.assert_not_called()
    now = 6.0
    app._on_snapshot(snap(Status.WATCHING, raw_status=Status.CLEAR))
    restore.assert_called_once_with({101: True, 102: False})


def test_medium_alert_never_runs_window_action(monkeypatch):
    minimize = Mock()
    monkeypatch.setattr(actions, "minimize_apps", minimize)
    app = app_with(Settings(action="minimize_app", target_apps=("a.exe",)))
    app._on_snapshot(snap(Status.ALERT, alert_started=True, alert_level=Level.MEDIUM))
    minimize.assert_not_called()


def test_hide_shortcut_works_while_disarmed():
    app = app_with(Settings(armed=False, target_apps=("a.exe",)))
    minimized = []
    restored = []

    def minimize(exes):
        minimized.extend(exes)
        return {101: True}

    def restore(hwnds):
        restored.extend(hwnds)
        return len(hwnds)

    app._hide_toggle = actions.HideToggle(minimize_selected=minimize, restore=restore)
    app.toggle_hidden_windows()
    app.toggle_hidden_windows()
    assert minimized == ["a.exe"]
    assert restored == [101]


def test_away_discards_alert_restore_even_when_display_is_smoothed(monkeypatch):
    restore = Mock()
    monkeypatch.setattr(actions, "restore_windows", restore)
    app = app_with(Settings(action="minimize_app", restore_after_alert=True))
    app._restore_plan.record({101: True})
    app._on_snapshot(snap(Status.WATCHING, raw_status=Status.AWAY))
    assert app._restore_plan.pending == {}
    restore.assert_not_called()
