"""Recover a real engine alert through the app without a camera or native actions."""

from collections import deque
from unittest.mock import Mock

import numpy as np
import pytest
from PySide6.QtCore import Qt

from desk_sentry import actions
from desk_sentry.config import Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.engine import Engine, _LoopState
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with


@pytest.mark.parametrize("interruption", ["no_camera", "error"])
@pytest.mark.parametrize("already_executed", [False, True])
def test_engine_app_recovery_reconfirms_pending_but_never_repeats_executed_action(
    monkeypatch, interruption, already_executed
):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now[0])
    execute = Mock()
    monkeypatch.setattr(actions, "run_action", execute)
    settings = Settings(action="lock", sound=False, exclude_self=False, stationary_mute_seconds=0)
    controller = app_with(settings)
    events = deque()
    snapshots = []
    current = {"kind": "empty"}

    class Camera:
        index, connected, fps = 0, True, 30.0
        last_frame_at = None

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self, after_seq, timeout):
            timestamp, kind = events.popleft()
            now[0], current["kind"] = timestamp, kind
            if kind == "no_camera":
                return after_seq, None  # connected stays True; the alert engine is not reset.
            self.last_frame_at = timestamp
            return after_seq + 1, np.full((360, 640, 3), (after_seq + 1) * 15, dtype=np.uint8)

    class Detector:
        def detect(self, frame, score_threshold=0.4):
            if current["kind"] == "error":
                raise MemoryError("injected during confirmation")
            if current["kind"] == "empty":
                return []
            return [Detection(Box(100, 50, 180, 300), 0.9)]

    def receive(snapshot):
        controller._on_snapshot(snapshot)
        snapshots.append(snapshot)

    engine = Engine(Detector(), settings, receive, camera_factory=lambda _index: Camera())
    controller.engine = engine
    state = _LoopState()

    def inject(timestamp, kind="person"):
        previous = len(snapshots)
        events.append((timestamp, kind))
        # Exercise the real engine/app step on the owning test thread. The old
        # harness raced worker startup/first OpenCV work against a 2s wall clock
        # while its domain clock was frozen, and ran GUI-facing callbacks on the
        # worker. No sleeps or wall-time handshake belong in confirmation tests.
        engine._step(state)
        assert len(snapshots) == previous + 1
        return snapshots[-1]

    try:
        inject(0.5, "empty")
        inject(2.5)
        started = inject(2.65)
        assert started.alert_started and started.evade_near
        execute.assert_not_called()
        if already_executed:
            inject(3.16)
            execute.assert_called_once_with("lock")
        outage_at = 6.3 if interruption == "no_camera" else 3.3 if already_executed else 2.8
        outage = inject(outage_at, interruption)
        assert outage.status is (Status.NO_CAMERA if interruption == "no_camera" else Status.ERROR)
        assert execute.call_count == int(already_executed)
        recovered = inject(outage_at + 0.2)
        if interruption == "no_camera":
            recovered = inject(outage_at + 0.35)
            assert recovered.continued  # Tracker expired during the gap: merged, no new start.
        assert recovered.raw_status in (Status.ALERT, Status.APPROACHING)
        assert not recovered.alert_started
        recovered_at = now[0]
        inject(recovered_at + 0.3)
        assert execute.call_count == int(already_executed)
        inject(recovered_at + 0.51)
        inject(recovered_at + 0.7)
        assert execute.call_count == 1, " / ".join(
            f"{snap.status.value}:{snap.raw_status}:{int(snap.alert_started)}:"
            f"{int(snap.continued)}:{int(snap.evade_near)}"
            for snap in snapshots
        )
        execute.assert_called_once_with("lock")
        assert engine.error_count == int(interruption == "error")
    finally:
        engine.stop()


def test_cover_picker_shows_exe_and_keeps_full_window_title_in_tooltip(qtbot):
    title = "工作！完整標題" * 12
    window = MainWindow(
        Settings(cover_app="notepad.exe"),
        lambda _settings: None,
        list_apps=lambda: [("notepad.exe", title), ("calc.exe", "計算機")],
    )
    qtbot.addWidget(window)
    picker = window.cover_app
    assert picker.currentText() == "notepad.exe"
    assert picker.toolTip() == title
    assert picker.itemData(picker.currentIndex(), Qt.ItemDataRole.ToolTipRole) == title
    assert picker.width() == theme.D["dropdown_width"]
    picker.setCurrentIndex(picker.findData("calc.exe"))
    assert picker.currentText() == "calc.exe" and picker.toolTip() == "計算機"
    picker.setCurrentIndex(0)
    assert picker.toolTip() == ""
