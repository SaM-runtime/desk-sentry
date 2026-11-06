"""Release completion, not pause intent, is the observable yield boundary."""

import threading
from dataclasses import replace

import numpy as np
import pytest

from desk_sentry.camera import Camera
from desk_sentry.config import Settings
from desk_sentry.engine import Engine, _LoopState
from desk_sentry.sentry import Status
from test_status_integration import FrameDetector


@pytest.mark.parametrize("reason", ["meeting", "manual_yield"])
def test_yield_ack_waits_for_capture_release_and_pause_never_reopens(monkeypatch, reason):
    release_entered = threading.Event()
    allow_release = threading.Event()
    released = threading.Event()
    acknowledged = threading.Event()
    opens = []

    class Capture:
        def read(self):
            return True, np.zeros((60, 80, 3), dtype=np.uint8)

        def release(self):
            release_entered.set()
            assert allow_release.wait(8)
            released.set()

    def capture():
        opens.append(1)
        return Capture()

    camera = Camera(0, capture_factory=capture)
    user = Settings(armed=True, camera_mode="exclusive", exclude_self=False)
    observations = []

    def observe(snapshot):
        observations.append((snapshot, released.is_set()))
        if snapshot.pause_reason == reason:
            acknowledged.set()

    engine = Engine(
        FrameDetector([[]]),
        user,
        observe,
        camera_factory=lambda index: camera,
    )
    monkeypatch.setattr(engine._wake, "wait", lambda timeout: True)
    state = _LoopState()
    worker = None
    try:
        engine._step(state)
        observations.clear()
        request = engine.apply(replace(user, armed=False), pause_reason=reason)
        worker = threading.Thread(target=engine._step, args=(state,))
        worker.start()
        assert release_entered.wait(2)
        # Cross the old 3-second join timeout while release is deliberately blocked.
        assert not acknowledged.wait(3.2)
        assert not observations
        allow_release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        snapshot, was_released = observations[-1]
        assert was_released and snapshot.status is Status.PAUSED
        assert snapshot.pause_reason == reason
        assert snapshot.pause_request == request
        for _ in range(3):
            engine._step(state)
        assert opens == [1]
        assert user.armed is True
    finally:
        allow_release.set()
        if worker is not None:
            worker.join(timeout=3)
        engine.stop()


def test_release_timeout_keeps_engine_alive_until_release_is_confirmed(monkeypatch):
    release_allowed = threading.Event()
    retried = threading.Event()
    acknowledged = threading.Event()
    errors = []

    class SlowCamera:
        index = 0
        connected = False
        fps = 0.0
        attempts = 0

        def start(self):
            pass

        def latest(self, after_seq, timeout):
            return after_seq, None

        def stop(self):
            self.attempts += 1
            if self.attempts >= 3:
                retried.set()
            if not release_allowed.is_set():
                raise TimeoutError("driver is still releasing")

    camera = SlowCamera()
    user = Settings(armed=True, camera_mode="exclusive")

    def observe(snapshot):
        if snapshot.status is Status.ERROR:
            errors.append(snapshot)
        if snapshot.pause_reason == "meeting":
            acknowledged.set()

    engine = Engine(FrameDetector([[]]), user, observe, camera_factory=lambda _: camera)
    engine._step(_LoopState())
    engine.apply(replace(user, armed=False), pause_reason="meeting")
    monkeypatch.setattr("desk_sentry.engine.ERROR_RETRY_SECONDS", 0.01)
    engine.start()
    try:
        assert retried.wait(2), "release failure killed the engine worker"
        assert errors and not acknowledged.is_set()
        release_allowed.set()
        assert acknowledged.wait(2)
    finally:
        release_allowed.set()
        engine.stop()
