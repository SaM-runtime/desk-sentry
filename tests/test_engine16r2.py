"""TASK16r2: release failure survives retry and pause until a new frame."""

from dataclasses import replace

from desk_sentry.config import Settings
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.sentry import Status
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with
from test_engine16 import CameraStub, Detector
from test_engine16r import FAILURE_TEXT
from test_meeting_ui14 import TimerRecorder


def _failed_camera():
    camera = CameraStub(fatal=True)
    camera.release_failed = True
    camera.fatal_error = camera.failure_reason = "OSError"
    return camera


def test_release_failure_survives_armed_backoff_pause_and_no_frame_reopen(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: clock[0])
    replacement = CameraStub(frame=False)
    cameras = iter((_failed_camera(), replacement))
    created = []

    def factory(_index):
        camera = next(cameras)
        created.append(camera)
        return camera

    snapshots = []
    initial = Settings(exclude_self=False)
    engine = Engine(Detector(), initial, snapshots.append, camera_factory=factory)
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    state = _LoopState()
    engine._step(state)
    retry_at = engine._camera_retry_at
    assert snapshots[-1].health_reason == "camera_release_failed"
    clock[0] += 1
    engine._step(state)
    assert snapshots[-1].health_reason == "camera_release_failed"
    assert len(created) == 1 and engine._camera_retry_at == retry_at

    request = engine.apply(replace(initial, armed=False), pause_reason="manual_yield")
    engine._step(state)
    assert snapshots[-1].status is Status.PAUSED
    assert snapshots[-1].health_reason == "camera_release_failed"
    assert snapshots[-1].pause_reason is None and snapshots[-1].pause_request != request
    engine.apply(initial)
    clock[0] = retry_at + 0.1
    engine._step(state)
    assert engine._camera is replacement
    assert snapshots[-1].health_reason == "camera_release_failed"
    replacement._frame = replacement.connected = True
    clock[0] += 1
    engine._step(state)
    assert snapshots[-1].health_reason != "camera_release_failed"
    assert engine._camera_release_failed is False
    engine.stop()


def test_manual_yield_in_fatal_backoff_never_acknowledges_before_new_frame(qtbot, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: clock[0])
    initial = Settings(exclude_self=False)
    app = app_with(initial)
    window = MainWindow(initial, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    app.window = window
    app._schedule_timer = TimerRecorder()
    app._locked = False
    replacement = CameraStub(frame=False)
    cameras = iter((_failed_camera(), replacement))
    snapshots = []

    def observe(snapshot):
        snapshots.append(snapshot)
        app._on_snapshot(snapshot)

    engine = Engine(Detector(), initial, observe, camera_factory=lambda _index: next(cameras))
    app.engine = engine
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    state = _LoopState()
    try:
        engine._step(state)
        retry_at = engine._camera_retry_at
        assert window.camera_status.text() == FAILURE_TEXT
        app._manual_yield = True
        app._apply_monitoring()
        engine._step(state)
        assert app._pause_ack_reason is None
        assert snapshots[-1].pause_reason is None and snapshots[-1].pause_request == 0
        assert window.camera_status.text() == FAILURE_TEXT

        app._manual_yield = False
        app._apply_monitoring()
        assert window.camera_status.text() == FAILURE_TEXT
        app._on_snapshot(Snapshot(Status.PAUSED, camera_backend="shared"))
        assert window.camera_status.text() == FAILURE_TEXT
        clock[0] += 1
        engine._step(state)
        assert window.camera_status.text() == FAILURE_TEXT
        clock[0] = retry_at + 0.1
        engine._step(state)
        assert engine._camera is replacement and not replacement.connected
        assert window.camera_status.text() == FAILURE_TEXT
        replacement._frame = replacement.connected = True
        clock[0] += 1
        engine._step(state)
        assert snapshots[-1].health_reason != "camera_release_failed"
        assert window.camera_status.text() == "共用中，可與視訊會議同時使用"
    finally:
        engine.stop()
