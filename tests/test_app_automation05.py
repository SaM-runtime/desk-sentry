"""Application integration of user intent, pause sources and action boundaries."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from desk_sentry import actions, schedule
from desk_sentry.app import DeskSentryApp, _ObservedEvadePlan
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.health import HealthState
from desk_sentry.people import Level
from desk_sentry.sentry import Status
from desk_sentry.stats import AlertStats
from desk_sentry.ui import theme
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow
from desk_sentry.ui.history import AlertHistory, HistoryWindow
from test_app_hide04 import app_with
from test_session05 import FakeApp, FakeBackend, dispatch

pytestmark = pytest.mark.usefixtures("qapp")


class TimerRecorder:
    def __init__(self):
        self.interval = None

    def stop(self):
        self.interval = None

    def start(self, interval):
        self.interval = interval


class EngineRecorder:
    def __init__(self):
        self.settings = None
        self.reason = None
        self.diagnostics = None
        self.exclusive_allowed = True

    def set_exclusive_allowed(self, allowed):
        self.exclusive_allowed = allowed

    def apply(self, settings, *, pause_reason):
        self.settings = settings
        self.reason = pause_reason


class ActionRecorder:
    def __init__(self):
        self.text = ""
        self.visible = False
        self.enabled = False

    def setText(self, text):  # noqa: N802
        self.text = text

    def setVisible(self, visible):  # noqa: N802
        self.visible = visible


class TrayRecorder:
    def __init__(self):
        self.tooltip = ""

    def setIcon(self, icon):  # noqa: N802
        pass

    def setToolTip(self, text):  # noqa: N802
        self.tooltip = text


def controller(settings, now=datetime(2026, 10, 12, 12)):
    app = DeskSentryApp.__new__(DeskSentryApp)
    app.window = SimpleNamespace(
        settings=settings,
        set_pause_reason=lambda reason, **_context: None,
        set_camera_status=lambda *_args, **_kwargs: None,
        set_camera_diagnostics=lambda _text: None,
    )
    app._wall_now = lambda: now
    app._locked = False
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
    app._build_info = SimpleNamespace(display_version="test", version="test")
    app._available_update = None
    app._discreet_active = False
    app._pause_reason = None
    app._pause_ack_reason = None
    app._pause_request = 0
    app._effective_armed = settings.armed
    app._health_state = HealthState.OK
    app._evade_plan = _ObservedEvadePlan()
    app._last_status = Status.CLEAR
    app.engine = EngineRecorder()
    app._schedule_timer = TimerRecorder()
    app.tray = TrayRecorder()
    app._arm_action = ActionRecorder()
    app._pause_info = ActionRecorder()
    return app


@pytest.mark.parametrize(
    "armed,locked,inside,expected,reason",
    [
        (False, False, False, False, "manual"),
        (False, False, True, False, "manual"),
        (False, True, False, False, "manual"),
        (False, True, True, False, "manual"),
        (True, False, False, False, "schedule"),
        (True, False, True, True, None),
        (True, True, False, False, "locked"),
        (True, True, True, False, "locked"),
    ],
)
def test_effective_monitoring_combinations_preserve_user_setting(
    armed, locked, inside, expected, reason
):
    user = Settings(armed=armed, schedule_enabled=True, schedule_end="18:30" if inside else "09:30")
    app = controller(user)
    app._locked = locked
    app._apply_monitoring()
    assert app.engine.settings.armed is expected
    assert app.window.settings is user and user.armed is armed
    assert app._pause_reason == reason
    assert app._pause_info.visible is (reason in ("locked", "schedule"))
    assert not app._pause_info.enabled
    if reason == "locked":
        assert app.tray.tooltip.endswith("已暫停 · 電腦鎖定中")
    elif reason == "schedule":
        assert app.tray.tooltip.endswith("已暫停 · 非監看時段")


def test_fake_wts_changes_effective_value_and_unlock_obeys_schedule():
    user = Settings(schedule_enabled=True)
    app = controller(user)
    native_app = FakeApp()
    notifications = schedule.SessionNotifications(app._session_changed, backend=FakeBackend())
    notifications.install(native_app, 1234)
    dispatch(native_app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_LOCK)
    assert not app.engine.settings.armed and app.engine.reason == "locked"
    app._wall_now = lambda: datetime(2026, 10, 12, 19)
    dispatch(native_app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_UNLOCK)
    assert not app.engine.settings.armed and app.engine.reason == "schedule"
    assert user.armed is True
    notifications.close()


def test_one_boundary_timer_and_disabled_lock_policy():
    app = controller(Settings(schedule_enabled=True, pause_when_locked=False))
    app._locked = True
    timer = app._schedule_timer
    app._apply_monitoring()
    assert app.engine.settings.armed
    assert timer.interval == 23_400_000  # noon -> today's 18:30, no polling interval
    app.window.settings.schedule_enabled = False
    app._apply_monitoring()
    assert app._schedule_timer is timer and timer.interval is None


def test_new_settings_do_not_expand_existing_diagnostics_schema(tmp_path):
    user = Settings(diagnostics=True, schedule_enabled=True, schedule_end="09:30")
    app = controller(user)
    app._path = tmp_path / "settings.json"
    records = []
    app._diagnostics = SimpleNamespace(write=lambda kind, **data: records.append((kind, data)))
    app.popup = SimpleNamespace(corner="", mirror_width=0)
    app._on_settings(user)
    original_fields = {
        "camera_index",
        "armed",
        "sensitivity",
        "exclude_self",
        "self_zone",
        "stationary_mute_seconds",
        "alert_far_passersby",
        "unknown_level",
        "show_mirror",
        "edge_flash",
        "sound",
        "action",
        "target_apps",
        "restore_after_alert",
        "mirror_corner",
        "mirror_width",
        "diagnostics",
        "mirror_hotkey",
        "hide_hotkey",
        "compact_mode",
        "compact_geometry",
    }
    for _kind, data in records:
        assert set(data) - {"version"} == original_fields
        assert data["armed"] is True
    assert {kind for kind, data in records} == {"diagnostics_on", "settings"}
    assert not app.engine.settings.armed


def test_snapshot_away_action_and_counters_are_separate_from_alert_restore(tmp_path, monkeypatch):
    now = 0.0
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now)
    monkeypatch.setattr(actions, "last_input_age", lambda: 120.0)
    executed = []
    monkeypatch.setattr(
        actions, "run_action", lambda action, targets=(): executed.append((action, targets))
    )
    settings = Settings(
        away_action="minimize_app",
        away_minutes=1,
        target_apps=("mail.exe",),
        restore_after_alert=True,
        action="minimize_app",
        edge_flash=False,
        sound=False,
    )
    app = app_with(settings)
    app._away_plan = actions.AwayPlan()
    app._wall_now = lambda: datetime(2026, 10, 12, 14)
    app.stats = AlertStats(tmp_path / "stats.json", clock=app._wall_now)
    app._stats_day = app._wall_now().date()
    app._history_window = None
    app._on_snapshot(Snapshot(Status.AWAY, raw_status=Status.AWAY))
    now = 60.0
    app._on_snapshot(Snapshot(Status.AWAY, raw_status=Status.AWAY))
    assert executed == [("minimize_app", ("mail.exe",))]
    assert app._restore_plan.pending == {}
    app._on_snapshot(Snapshot(Status.ALERT, alert_started=True, alert_level=Level.MEDIUM))
    app._on_snapshot(Snapshot(Status.ALERT, continued=True, alert_level=Level.MEDIUM))
    assert app.stats.summary().today == 1


def test_alert_curtain_can_be_closed_by_minimize_style_hotkey_and_restores_mini(monkeypatch, qtbot):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        _CurtainWindow,
        "showFullScreen",
        lambda window: window.setWindowState(Qt.WindowState.WindowFullScreen),
    )
    monkeypatch.setattr(_CurtainWindow, "activateWindow", lambda window: None)
    settings = Settings(
        action="curtain", hide_hotkey_style="minimize", edge_flash=False, sound=False
    )
    app = app_with(settings)
    app.curtain = CurtainGroup(mirror=app.mini, on_hidden=app._curtain_hidden)
    app._on_snapshot(
        Snapshot(Status.ALERT, alert_started=True, alert_level=Level.HIGH, evade_near=True)
    )
    now[0] = 0.4
    app._on_snapshot(Snapshot(Status.ALERT, alert_level=Level.HIGH, evade_near=True))
    qtbot.addWidget(app.curtain.windows[0])
    assert app.curtain.visible
    app.mini.show.assert_called_once()
    app.toggle_hidden_windows()
    assert not app.curtain.visible
    app.mini.hide.assert_called_once()
    settings.hide_hotkey_style = "curtain"
    app.toggle_hidden_windows()
    assert app.curtain.visible
    app.curtain.windows[0].keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    assert not app.curtain.visible


def test_summary_tracks_hour_and_day_changes_without_a_new_alert(tmp_path, qtbot):
    now = datetime(2026, 10, 12, 23)
    app = app_with(Settings())
    app._wall_now = lambda: now
    app.stats = AlertStats(tmp_path / "stats.json", clock=app._wall_now)
    app.stats.record(alert_started=True)
    app._stats_day = now.date()
    app._stats_hour = now.hour
    app._history_window = HistoryWindow(AlertHistory(), stats=app.stats)
    qtbot.addWidget(app._history_window)
    app._history_window.refresh()
    assert app._history_window.today_summary.text() == "今天 1 次"
    now = datetime(2026, 10, 13, 0)
    app._record_stats(Snapshot(Status.PAUSED))
    assert app._history_window.today_summary.text() == "今天 0 次"
    assert app._history_window.hourly_chart.hour_color(0) == theme.ACCENT
    assert app._history_window.hourly_chart.hour_color(23) == theme.TEXT_2
    now = datetime(2026, 10, 13, 1)
    app._record_stats(Snapshot(Status.PAUSED))
    assert app._history_window.hourly_chart.hour_color(1) == theme.ACCENT
    assert app._history_window.hourly_chart.hour_color(0) == theme.TEXT_2
