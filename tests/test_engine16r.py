"""TASK16r: failed release can never acknowledge a camera handoff."""

import gzip
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from desk_sentry.camera import Camera
from desk_sentry.config import Settings
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.health import HealthState
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with
from test_engine16 import CameraStub, Detector

FAILURE_TEXT = "無法確認已讓出攝影機；若會議軟體拿不到畫面，請結束 Desk Sentry"


@pytest.mark.parametrize("already_ended", [False, True])
def test_failed_release_blocks_pause_ack_until_monitoring_resumes(monkeypatch, already_ended):
    class ReleaseFailureCamera(CameraStub):
        def __init__(self):
            super().__init__()
            self.release_failed = already_ended
            self.fatal_error = "OSError" if already_ended else None
            self.failure_reason = self.fatal_error
            self.worker_alive = not already_ended

        def stop(self):
            self.release_failed = True
            self.worker_alive = False
            self.fatal_error = self.failure_reason = "OSError"
            raise RuntimeError("camera worker failed while releasing resources")

    snapshots = []
    initial = Settings(exclude_self=False)
    engine = Engine(
        Detector(),
        initial,
        snapshots.append,
        camera_factory=lambda index: CameraStub(index, frame=True),
    )
    engine._camera = ReleaseFailureCamera()
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    engine.apply(replace(initial, armed=False), pause_reason="manual_yield")
    state = _LoopState()

    engine._step(state)
    engine._step(state)

    assert engine._camera is None
    assert len(snapshots) == 2
    assert all(snapshot.status is Status.PAUSED for snapshot in snapshots)
    assert all(
        snapshot.pause_reason is None and snapshot.pause_request == 0 for snapshot in snapshots
    )
    assert all(snapshot.health_reason == "camera_release_failed" for snapshot in snapshots)
    assert all(snapshot.health_state is HealthState.DEGRADED for snapshot in snapshots)
    engine.apply(initial)
    engine._step(state)
    assert snapshots[-1].health_reason != "camera_release_failed"


def test_failed_release_snapshot_cannot_confirm_handoff_in_app(qtbot):
    app = app_with(Settings(camera_mode="exclusive"))
    window = MainWindow(app.window.settings, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    app.window = window
    app._pause_reason = "manual_yield"
    app._pause_request = 9
    app._effective_armed = False
    window.set_pause_reason("manual_yield", acknowledged=False)

    # Even an accidentally matching token must not override the failure reason.
    app._on_snapshot(
        Snapshot(
            Status.PAUSED,
            pause_reason="manual_yield",
            pause_request=9,
            health_reason="camera_release_failed",
        )
    )

    assert app._pause_ack_reason is None
    assert window.camera_status.text() == FAILURE_TEXT
    assert theme.STATUS_COLOR[Status.ERROR] in window.camera_status_dot.styleSheet()
    app._on_snapshot(Snapshot(Status.PAUSED, pause_reason="manual_yield", pause_request=9))
    assert app._pause_ack_reason is None
    assert window.camera_status.text() == FAILURE_TEXT
    app._pause_reason = None
    app._effective_armed = True
    window.set_pause_reason(None)
    app._on_snapshot(Snapshot(Status.CLEAR, camera_backend="shared"))
    assert window.camera_status.text() == "共用中，可與視訊會議同時使用"
    assert theme.STATUS_COLOR[Status.CLEAR] in window.camera_status_dot.styleSheet()


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("locked", "已暫停 · 電腦鎖定中"),
        ("schedule", "已暫停 · 非監看時段"),
    ],
)
@pytest.mark.parametrize("backend", ["shared", "exclusive"])
def test_app_passes_effective_pause_to_camera_status(qtbot, reason, expected, backend):
    app = app_with(Settings())
    window = MainWindow(app.window.settings, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    app.window = window
    app._pause_reason = reason
    app._effective_armed = False
    window.set_pause_reason(reason)

    app._on_snapshot(Snapshot(Status.PAUSED, camera_backend=backend, pause_reason=reason))

    assert window.camera_status.text() == expected
    assert theme.STATUS_COLOR[Status.PAUSED] in window.camera_status_dot.styleSheet()


def test_engine_pause_failure_reaches_app_without_ack(qtbot, monkeypatch):
    app = app_with(Settings(camera_mode="exclusive", exclude_self=False))
    window = MainWindow(app.window.settings, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    app.window = window
    app._pause_reason = "manual_yield"
    app._effective_armed = False
    window.set_pause_reason("manual_yield", acknowledged=False)
    engine = Engine(Detector(), window.settings, app._on_snapshot)
    engine._camera = SimpleNamespace(
        index=0, release_failed=True, fatal_error="OSError", worker_alive=False
    )
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    app._pause_request = engine.apply(
        replace(window.settings, armed=False), pause_reason="manual_yield"
    )

    engine._step(_LoopState())

    assert app._pause_ack_reason is None
    assert window.camera_status.text() == FAILURE_TEXT


@pytest.mark.parametrize("mode", ["auto", "exclusive"])
def test_camera_release_error_survives_full_handoff_path_and_resume(
    qtbot, monkeypatch, caplog, tmp_path, mode
):
    class Capture:
        def open(self):
            pass

        def read(self):
            time.sleep(0.001)
            return True, np.zeros((60, 80, 3), dtype=np.uint8)

        def release(self):
            raise OSError("private-device-name private-device-id private-path")

    options = (
        {"shared_factory": lambda *_args: Capture()}
        if mode == "auto"
        else {"capture_factory": Capture}
    )
    camera = Camera(0, mode=mode, **options)
    initial = Settings(
        camera_mode=mode,
        exclude_self=False,
        camera_id="private-device-id",
        camera_name="private-device-name",
    )
    app = app_with(initial)
    window = MainWindow(initial, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    app.window = window
    snapshots = []

    def observe(snapshot):
        snapshots.append(snapshot)
        app._on_snapshot(snapshot)

    cameras = iter((camera, CameraStub(frame=True)))
    engine = Engine(Detector(), initial, observe, camera_factory=lambda _index: next(cameras))
    engine.diagnostics = DiagnosticsLog(tmp_path)
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    state = _LoopState()
    try:
        engine._step(state)
        app._pause_reason = "manual_yield"
        app._effective_armed = False
        window.set_pause_reason("manual_yield", acknowledged=False)
        app._pause_request = engine.apply(
            replace(initial, armed=False), pause_reason="manual_yield"
        )
        engine._step(state)
        engine._step(state)

        assert camera.release_failed is True
        assert all(snapshot.health_reason == "camera_release_failed" for snapshot in snapshots[-2:])
        assert all(
            snapshot.pause_reason is None and snapshot.pause_request == 0
            for snapshot in snapshots[-2:]
        )
        assert app._pause_ack_reason is None
        assert window.camera_status.text() == FAILURE_TEXT
        engine.diagnostics.flush()
        events = [
            json.loads(line)
            for path in tmp_path.glob("*.jsonl.gz")
            for line in gzip.decompress(path.read_bytes()).splitlines()
        ]
        exported = json.dumps(events) + app._camera_diagnostics_text() + caplog.text
        assert not any(
            value in exported
            for value in ("private-device-name", "private-device-id", "private-path")
        )

        app._pause_reason = None
        app._effective_armed = True
        window.set_pause_reason(None)
        engine.apply(initial)
        engine._step(state)
        assert snapshots[-1].health_reason != "camera_release_failed"
        assert window.camera_status.text() == "共用中，可與視訊會議同時使用"
    finally:
        engine.stop()
