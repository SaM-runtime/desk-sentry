"""Auto reconnect must re-negotiate shared capture instead of staying exclusive."""

import threading
import time

import numpy as np
import pytest

from desk_sentry.camera import Camera


class SharedFrames:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.released = False

    def open(self):
        if self.fail:
            raise PermissionError("shared unavailable on first open")

    def read(self):
        time.sleep(0.001)
        return True, np.full((2, 3, 3), 42, dtype=np.uint8)

    def release(self):
        self.released = True


@pytest.mark.parametrize("exclusive_missing", [False, True])
def test_auto_retries_shared_after_exclusive_camera_outage(exclusive_missing):
    attempts = []
    events = []
    recovered = threading.Event()
    ticks = [0.0]

    def clock():
        ticks[0] += 4.0
        return ticks[0]

    class LostCapture:
        def read(self):
            return False, None

        def release(self):
            events.append("exclusive_released")

    def shared_factory(*_args):
        capture = SharedFrames(fail=not attempts)
        attempts.append(capture)
        if len(attempts) == 2:
            if not exclusive_missing:
                assert events == ["exclusive_released"]
            recovered.set()
        return capture

    camera = Camera(
        0,
        shared_factory=shared_factory,
        capture_factory=lambda: None if exclusive_missing else LostCapture(),
        retry_wait=lambda _: False,
        clock=clock,
    )
    camera.start()
    try:
        assert recovered.wait(1), "auto stayed on its exclusive retry path"
        seq, frame = camera.latest(0, 1)
        assert seq > 0 and frame is not None
        assert frame.shape == (2, 3, 3) and frame[0, 0].tolist() == [42, 42, 42]
        assert camera.backend_name == "shared"
        assert camera.failure_reason is None
    finally:
        camera.stop()
    assert all(capture.released for capture in attempts)


def test_auto_stop_and_start_retries_shared_before_exclusive():
    opened = []

    def shared_factory(*_args):
        capture = SharedFrames()
        opened.append(capture)
        return capture

    camera = Camera(
        0,
        shared_factory=shared_factory,
        capture_factory=lambda: pytest.fail("shared success must not open exclusive"),
    )
    seq = 0
    for _ in range(2):
        camera.start()
        try:
            next_seq, frame = camera.latest(seq, 1)
            assert next_seq > seq and frame is not None
            seq = next_seq
            assert camera.backend_name == "shared"
        finally:
            camera.stop()
            seq = camera.latest(seq, 0)[0]
    assert len(opened) == 2
    assert all(capture.released for capture in opened)


def test_auto_outage_retries_keep_backoff_across_shared_attempts():
    waits = []
    shared_attempts = []
    finished = threading.Event()

    def retry(delay):
        waits.append(delay)
        if len(waits) == 4:
            finished.set()
            return True
        return False

    def shared_factory(*_args):
        capture = SharedFrames(fail=True)
        shared_attempts.append(capture)
        return capture

    camera = Camera(
        0,
        shared_factory=shared_factory,
        capture_factory=lambda: None,
        retry_wait=retry,
    )
    camera.start()
    try:
        assert finished.wait(1)
    finally:
        camera.stop()
    assert waits == [2, 5, 10, 30]
    assert len(shared_attempts) == 4
    assert all(capture.released for capture in shared_attempts)
