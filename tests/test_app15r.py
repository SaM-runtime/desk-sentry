"""TASK-15r app/engine camera identity and release acknowledgement regressions."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from desk_sentry.app import _diagnostics_settings
from desk_sentry.config import Settings
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.sentry import Status
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with


class Detector:
    def detect(self, _frame, score_threshold=0.4):
        return []


class CameraStub:
    def __init__(self, index: int) -> None:
        self.index = index
        self.connected = False
        self.fps = 30.0
        self.backend_name = "shared"
        self.backend_detail = "winrt"
        self.subtype = "NV12"
        self.width = 1280
        self.height = 720
        self.failure_reason = None
        self.fatal_error = None
        self.worker_alive = True
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def latest(self, after_seq: int, timeout: float):
        return after_seq, None


class DiagnosticsRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write(self, kind: str, **fields: object) -> None:
        self.records.append({"kind": kind, **fields})


def test_engine_passes_camera_id_and_reopens_when_identity_changes(monkeypatch) -> None:
    created: list[tuple[CameraStub, dict[str, object]]] = []

    def camera(index: int, **kwargs: object) -> CameraStub:
        instance = CameraStub(index)
        created.append((instance, kwargs))
        return instance

    monkeypatch.setattr("desk_sentry.engine.Camera", camera)
    initial = Settings(camera_id="device-alpha", camera_name="USB Camera")
    engine = Engine(Detector(), initial, lambda _snapshot: None)

    engine._step(_LoopState())
    first, kwargs = created[0]
    engine.apply(replace(initial, camera_id="device-beta"))
    engine._sync_settings()

    assert kwargs["camera_id"] == "device-alpha"
    assert first.stopped and engine._camera is None


def test_camera_id_is_excluded_from_diagnostics_events_and_clipboard(monkeypatch) -> None:
    secret_id = "device-private-token"
    settings = Settings(camera_id=secret_id, camera_name="USB Camera", diagnostics=True)

    def camera(index: int, **_kwargs: object) -> CameraStub:
        return CameraStub(index)

    monkeypatch.setattr("desk_sentry.engine.Camera", camera)
    engine = Engine(Detector(), settings, lambda _snapshot: None)
    recorder = DiagnosticsRecorder()
    engine.diagnostics = recorder  # type: ignore[assignment]
    engine._step(_LoopState())
    engine._emit_snapshot(Snapshot(Status.WATCHING))

    app = app_with(settings)
    app._build_info = SimpleNamespace(version="2.0.2", display_version="2.0.2")
    copied = app._camera_diagnostics_text()
    events = json.dumps(recorder.records, ensure_ascii=False)

    assert "camera_id" not in _diagnostics_settings(settings)
    assert "camera_id" not in events and secret_id not in events
    assert "camera_id" not in copied and secret_id not in copied


def test_pause_status_waits_for_matching_paused_snapshot_acknowledgement() -> None:
    app = app_with(Settings())
    app._pause_reason = "manual_yield"
    app._pause_ack_reason = None
    app._pause_request = 7
    app._effective_armed = False
    app._camera_backend = "exclusive"

    app._refresh_camera_presentation()
    assert app.window.set_camera_status.call_args.kwargs["acknowledged"] is False

    app._on_snapshot(
        Snapshot(
            Status.PAUSED,
            camera_backend="exclusive",
            pause_reason="manual_yield",
            pause_request=7,
        )
    )

    assert app._pause_ack_reason == "manual_yield"
    assert app.window.set_camera_status.call_args.kwargs["acknowledged"] is True


def test_release_error_is_presented_without_acknowledging_pause() -> None:
    app = app_with(Settings())
    app._pause_reason = "meeting"
    app._pause_ack_reason = None
    app._pause_request = 3
    app._effective_armed = False
    app._meeting_active = True
    app._camera_backend = "exclusive"

    app._on_snapshot(
        Snapshot(
            Status.ERROR,
            camera_backend="exclusive",
            pause_reason="meeting",
            pause_request=3,
        )
    )

    status = app.window.set_camera_status.call_args.kwargs
    assert status["acknowledged"] is False
    assert status["unresponsive"] is True
    assert app._pause_ack_reason is None


def test_disconnected_error_reaches_camera_status() -> None:
    app = app_with(Settings())

    app._on_snapshot(
        Snapshot(
            Status.NO_CAMERA,
            camera_backend="pending",
            last_camera_error="CameraDisconnectedError",
        )
    )

    status = app.window.set_camera_status.call_args.kwargs
    assert status["last_error"] == "CameraDisconnectedError"
    assert status["unresponsive"] is True


def test_main_window_waits_for_release_ack_and_shows_release_failure(qtbot) -> None:
    window = MainWindow(Settings(), lambda _settings: None)
    qtbot.addWidget(window)
    window.set_pause_reason("manual_yield", acknowledged=False)

    window.set_camera_status("manual", acknowledged=False)
    assert window.camera_status.text() == "正在讓出攝影機…"

    window.set_camera_status("manual", acknowledged=True)
    assert window.camera_status.text() == "已讓出給視訊會議，結束後自動恢復"

    window.set_camera_status(
        "exclusive", meeting_active=True, unresponsive=True, acknowledged=False
    )
    assert window.camera_status.text() == "無法讓出攝影機，稍後自動重試"
