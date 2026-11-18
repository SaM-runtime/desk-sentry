"""Task 15 application wiring for camera yielding, status, and safe diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from desk_sentry.app import DeskSentryApp, _error_type, _execution_mode
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.sentry import Status
from test_app_hide04 import app_with


def _controller(settings: Settings | None = None) -> DeskSentryApp:
    chosen = settings or Settings()
    app = DeskSentryApp.__new__(DeskSentryApp)
    app.window = SimpleNamespace(
        settings=chosen,
        set_camera_status=Mock(),
        set_camera_diagnostics=Mock(),
    )
    app._build_info = SimpleNamespace(display_version="2.0.2", version="2.0.2")
    app._meeting_active = False
    app._manual_yield = False
    app._camera_mode = chosen.camera_mode
    app._camera_backend = "pending" if chosen.camera_mode == "auto" else "exclusive"
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
    app._last_status = Status.CLEAR
    return app


def test_existing_meeting_is_applied_before_camera_worker_starts(monkeypatch) -> None:
    events: list[str] = []

    class FakeMonitor:
        active = False

        def __init__(self, _callback) -> None:
            events.append("monitor-created")

        def poll_once(self) -> bool:
            events.append("meeting-polled")
            self.active = True
            return True

        def start(self) -> None:
            events.append("monitor-started")

    app = _controller(Settings(auto_yield_camera=True))
    app._bridge = SimpleNamespace(meeting=SimpleNamespace(emit=lambda _active: None))
    app.engine = SimpleNamespace(start=lambda: events.append("engine-started"))
    app._apply_monitoring = lambda: events.append(
        f"allowed={not (app._meeting_active and app.window.settings.auto_yield_camera)}"
    )
    monkeypatch.setattr("desk_sentry.app.meeting.MeetingMonitor", FakeMonitor)

    app._start_camera_monitoring()

    assert events == [
        "monitor-created",
        "meeting-polled",
        "allowed=False",
        "engine-started",
        "monitor-started",
    ]


def test_meeting_and_settings_update_exclusive_permission() -> None:
    app = _controller(Settings(camera_mode="auto", auto_yield_camera=True))
    allowed: list[bool] = []
    app.engine = SimpleNamespace(
        set_exclusive_allowed=allowed.append,
        apply=lambda *_args, **_kwargs: 1,
    )
    app._wall_now = __import__("datetime").datetime.now
    app._locked = False
    app._pause_reason = None
    app._pause_ack_reason = None
    app._pause_request = 0
    app._effective_armed = True
    app._evade_plan = Mock()
    app._schedule_timer = SimpleNamespace(stop=lambda: None, start=lambda _value: None)
    app._render_tray = Mock()
    app.window.set_pause_reason = Mock()

    app._meeting_changed(True)
    app.window.settings.auto_yield_camera = False
    app._apply_monitoring()

    assert allowed == [False, True]


def test_yielded_backend_pauses_until_debounced_meeting_end() -> None:
    app = _controller(Settings(camera_mode="auto", auto_yield_camera=True))
    app.engine = SimpleNamespace(set_exclusive_allowed=Mock(), apply=Mock(return_value=1))
    app._wall_now = __import__("datetime").datetime.now
    app._locked = False
    app._pause_reason = None
    app._pause_ack_reason = None
    app._pause_request = 0
    app._effective_armed = True
    app._evade_plan = Mock()
    app._schedule_timer = SimpleNamespace(stop=lambda: None, start=lambda _value: None)
    app._render_tray = Mock()
    app.window.set_pause_reason = Mock()
    app._camera_backend = "yielded"

    app._meeting_changed(True)
    assert app._pause_reason == "meeting"
    app._apply_monitoring()  # snapshots during the active/debounce period do not resume
    assert app._pause_reason == "meeting"
    app._meeting_changed(False)
    assert app._pause_reason is None


def test_camera_presentation_and_copy_text_use_only_allowlisted_values(monkeypatch) -> None:
    app = _controller(Settings(camera_mode="auto", auto_yield_camera=True, discreet=True))
    app._camera_backend = "shared"
    app._camera_backend_detail = "winrt"
    app._camera_subtype = "NV12"
    app._camera_width = 1920
    app._camera_height = 1080
    app._camera_fps = 29.96
    private_error = "\\".join(
        ("C:", "Users", "private", "USB Camera id=secret SharedCameraOperationStuckError")
    )
    app._last_camera_error = _error_type(private_error)
    app._camera_unresponsive = True
    app._meeting_active = True
    monkeypatch.setattr("desk_sentry.app.platform.version", lambda: "10.0.26100")
    monkeypatch.setattr("desk_sentry.app._process_execution_mode", lambda: "免安裝")

    app._refresh_camera_presentation()

    kwargs = app.window.set_camera_status.call_args.kwargs
    assert kwargs == {
        "backend_detail": "winrt",
        "subtype": "NV12",
        "width": 1920,
        "height": 1080,
        "fps": 29.96,
        "last_error": "UnknownError",
        "meeting_active": True,
        "unresponsive": True,
        "release_failed": False,
        "acknowledged": True,
    }
    text = app.window.set_camera_diagnostics.call_args.args[0]
    assert "版本：2.0.2" in text
    assert "Windows 版本：10.0.26100" in text
    assert "執行方式：免安裝" in text
    assert "解析度：1920x1080" in text
    assert "fps：實測 30.0／規格 未知" in text
    for private in ("Users", "private", "USB Camera", "secret", "id="):
        assert private not in text


def test_manual_pause_is_presented_as_paused_not_as_exclusive() -> None:
    app = _controller(Settings(camera_mode="exclusive"))
    app._pause_reason = "manual_yield"

    app._refresh_camera_presentation()

    assert app.window.set_camera_status.call_args.args == ("manual",)


def test_no_camera_snapshot_is_presented_as_unresponsive() -> None:
    app = app_with(Settings())

    app._on_snapshot(Snapshot(Status.NO_CAMERA, health_reason="no_frame"))

    assert app.window.set_camera_status.call_args.kwargs["unresponsive"] is True


def test_execution_mode_does_not_return_runtime_path(tmp_path) -> None:
    runtime = tmp_path / "DeskSentry"
    python = runtime / "python" / "pythonw.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (runtime / "啟動 Desk Sentry.cmd").write_text("", encoding="utf-8")
    assert _execution_mode(python) == "免安裝"
    (runtime / "unins000.exe").write_bytes(b"")
    assert _execution_mode(python) == "安裝版"
    assert str(tmp_path) not in _execution_mode(python)
