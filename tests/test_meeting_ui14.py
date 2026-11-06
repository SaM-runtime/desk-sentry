"""Task 14 settings, meeting priority and camera-release acknowledgement."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot

from desk_sentry.app import DeskSentryApp, _diagnostics_settings
from desk_sentry.config import Settings, load
from desk_sentry.engine import Snapshot
from desk_sentry.sentry import Status
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with

pytestmark = pytest.mark.usefixtures("qapp")


class TimerRecorder:
    def __init__(self) -> None:
        self.interval: int | None = None

    def stop(self) -> None:
        self.interval = None

    def start(self, interval: int) -> None:
        self.interval = interval


class EngineRecorder:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.reason: str | None = None
        self.request = 0

    def apply(self, settings: Settings, *, pause_reason: str) -> int:
        self.settings = settings
        self.reason = pause_reason
        self.request += 1
        return self.request


def controller(settings: Settings, *, hour: int = 12) -> DeskSentryApp:
    app = DeskSentryApp.__new__(DeskSentryApp)
    app.window = SimpleNamespace(settings=settings, set_pause_reason=Mock())
    app._wall_now = lambda: datetime(2026, 11, 5, hour)
    app._locked = False
    app._meeting_active = False
    app._manual_yield = False
    app._camera_backend = "pending" if settings.camera_mode == "auto" else "exclusive"
    app._pause_reason = None
    app._pause_ack_reason = None
    app._effective_armed = settings.armed
    app._last_status = Status.CLEAR
    app.engine = EngineRecorder()
    app._schedule_timer = TimerRecorder()
    app._render_tray = Mock()
    return app


def test_old_settings_default_to_auto_camera_and_meeting_yield(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"armed": false}', encoding="utf-8")

    old = load(path)
    invalid = Settings(camera_mode="invalid", auto_yield_camera="yes").normalized()

    assert (old.camera_mode, old.auto_yield_camera) == ("auto", True)
    assert (invalid.camera_mode, invalid.auto_yield_camera) == ("auto", True)


def test_camera_controls_render_and_report_changes(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = MainWindow(Settings(camera_mode="exclusive", auto_yield_camera=False), seen.append)
    qtbot.addWidget(window)

    assert window.camera_mode.currentData() == "exclusive"
    assert not window.auto_yield_camera.isChecked()
    window.camera_mode.setCurrentIndex(window.camera_mode.findData("auto"))
    window.auto_yield_camera.setChecked(True)

    assert (seen[-1].camera_mode, seen[-1].auto_yield_camera) == ("auto", True)


def test_camera_coordination_settings_do_not_expand_diagnostics_schema() -> None:
    fields = _diagnostics_settings(
        Settings(diagnostics=True, camera_mode="exclusive", auto_yield_camera=False)
    )

    assert "camera_mode" not in fields
    assert "auto_yield_camera" not in fields


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"armed": False, "manual": True, "locked": True, "meeting": True}, "manual"),
        ({"manual": True, "locked": True, "meeting": True}, "manual_yield"),
        ({"locked": True, "meeting": True}, "locked"),
        ({"hour": 20, "meeting": True}, "schedule"),
        ({"meeting": True}, "meeting"),
        ({"meeting": True, "backend": "shared"}, None),
        ({"meeting": True, "auto_yield": False}, None),
    ],
)
def test_pause_priority_and_meeting_require_exclusive_backend(changes, expected) -> None:
    settings = Settings(
        armed=changes.get("armed", True),
        auto_yield_camera=changes.get("auto_yield", True),
        pause_when_locked=True,
        schedule_enabled="hour" in changes,
        schedule_start="08:30",
        schedule_end="18:30",
    )
    app = controller(settings, hour=changes.get("hour", 12))
    app._manual_yield = changes.get("manual", False)
    app._locked = changes.get("locked", False)
    app._meeting_active = changes.get("meeting", False)
    app._camera_backend = changes.get("backend", "exclusive")

    app._apply_monitoring()

    assert app._pause_reason == expected
    assert app.engine.settings is not None
    assert app.engine.settings.armed is (expected is None)
    assert app.engine.reason == (expected or "manual")


def test_manual_yield_tray_toggle_requires_explicit_restore() -> None:
    app = controller(Settings())

    app.toggle_camera_yield()
    assert app._manual_yield and app._pause_reason == "manual_yield"

    app._meeting_active = True
    app._camera_backend = "exclusive"
    app.toggle_camera_yield()
    assert not app._manual_yield and app._pause_reason == "meeting"


def test_release_copy_waits_for_matching_engine_acknowledgement(qtbot: QtBot) -> None:
    settings = Settings(camera_mode="exclusive")
    app = controller(settings)
    app._meeting_active = True
    app._apply_monitoring()

    first = app.window.set_pause_reason.call_args
    assert first.args[0] == "meeting"
    assert first.kwargs["acknowledged"] is False

    integrated = app_with(settings)
    window = MainWindow(settings, lambda _settings: None)
    qtbot.addWidget(window)
    window.show()
    integrated.window = window
    integrated._effective_armed = False
    integrated._pause_reason = "meeting"
    integrated._pause_ack_reason = None
    integrated._pause_request = 1
    integrated._camera_backend = "exclusive"
    window.set_pause_reason("meeting", acknowledged=False)
    assert window.pause_reason.text() == "正在讓出攝影機"
    integrated._on_snapshot(
        Snapshot(
            Status.PAUSED,
            camera_backend="exclusive",
            pause_reason="meeting",
            pause_request=1,
        )
    )

    assert integrated._pause_ack_reason == "meeting"
    assert window.pause_reason.text() == "已暫停 · 會議中（已讓出攝影機）"

    integrated._pause_reason = "manual_yield"
    integrated._pause_ack_reason = None
    integrated._pause_request = 3
    window.set_pause_reason("manual_yield", acknowledged=False)
    integrated._on_snapshot(
        Snapshot(
            Status.PAUSED,
            camera_backend="exclusive",
            pause_reason="manual_yield",
            pause_request=1,
        )
    )
    assert integrated._pause_ack_reason is None
    assert window.pause_reason.text() == "正在讓出攝影機"

    integrated._on_snapshot(
        Snapshot(
            Status.PAUSED,
            camera_backend="exclusive",
            pause_reason="manual_yield",
            pause_request=3,
        )
    )
    assert integrated._pause_ack_reason == "manual_yield"
    assert window.pause_reason.text() == "已暫停 · 已讓出攝影機"
