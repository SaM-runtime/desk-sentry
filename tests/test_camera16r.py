"""TASK-16r camera release semantics and stop-race regressions."""

from __future__ import annotations

import threading
import time

import pytest

from desk_sentry.camera import Camera
from desk_sentry.camera_shared import SharedCameraOperationStuckError


def _eventually(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert predicate()


class IdleCapture:
    def open(self) -> None:
        return None

    def read(self):
        return False, None

    def release(self) -> None:
        return None


def test_stop_during_identity_probe_opens_no_camera_candidate(monkeypatch):
    matcher_started = threading.Event()
    matcher_allowed = threading.Event()
    opened = []

    def blocking_matcher(_camera_id, _camera_name, _timeout):
        matcher_started.set()
        assert matcher_allowed.wait(1.0)
        return True

    def open_winrt(*_args, **_kwargs):
        opened.append("winrt")
        return IdleCapture()

    monkeypatch.setattr("desk_sentry.camera._exclusive_capture", open_winrt)
    camera = Camera(
        0,
        camera_id="saved-id",
        mode="exclusive",
        sole_camera_matcher=blocking_matcher,
    )
    monkeypatch.setattr(
        camera,
        "_open_dshow",
        lambda _index: (opened.append("dshow"), IdleCapture())[1],
    )
    camera.start()
    assert matcher_started.wait(1.0)

    stop_error = []
    stop_done = threading.Event()

    def stop_camera():
        try:
            camera.stop()
        except Exception as exc:  # assertion reports the unexpected error below
            stop_error.append(exc)
        finally:
            stop_done.set()

    stopper = threading.Thread(target=stop_camera)
    stopper.start()
    assert not stop_done.wait(0.05)
    matcher_allowed.set()
    stopper.join(1.0)

    assert stop_done.is_set()
    assert stop_error == []
    assert opened == []


@pytest.mark.parametrize("mode", ["auto", "exclusive"])
def test_stop_during_capture_construction_never_calls_open(monkeypatch, mode):
    factory_started = threading.Event()
    factory_allowed = threading.Event()
    opened = []
    released = []

    class ConstructedCapture(IdleCapture):
        def open(self):
            opened.append(mode)

        def release(self):
            released.append(mode)

    def blocking_factory(*_args, **_kwargs):
        factory_started.set()
        assert factory_allowed.wait(1.0)
        return ConstructedCapture()

    if mode == "auto":
        camera = Camera(0, mode="auto", shared_factory=blocking_factory)
    else:
        monkeypatch.setattr("desk_sentry.camera._exclusive_capture", blocking_factory)
        camera = Camera(0, mode="exclusive")
    camera.start()
    assert factory_started.wait(1.0)

    stop_done = threading.Event()
    stop_error = []

    def stop_camera():
        try:
            camera.stop()
        except Exception as exc:
            stop_error.append(exc)
        finally:
            stop_done.set()

    stopper = threading.Thread(target=stop_camera)
    stopper.start()
    assert not stop_done.wait(0.05)
    factory_allowed.set()
    stopper.join(1.0)

    assert stop_done.is_set()
    assert stop_error == []
    assert opened == []
    assert released == [mode]


def test_shared_release_failure_has_distinct_sanitized_flag():
    reading = threading.Event()

    class FailedSharedRelease(IdleCapture):
        def read(self):
            reading.set()
            return False, None

        def release(self):
            raise OSError("private device path must remain hidden")

    camera = Camera(
        0,
        mode="auto",
        shared_factory=lambda *_args: FailedSharedRelease(),
        shared_timeout=10.0,
    )
    camera.start()
    assert reading.wait(1.0)

    with pytest.raises(RuntimeError):
        camera.stop()

    assert camera.release_failed is True
    assert camera.failure_reason == "OSError"
    assert camera.fatal_error == "OSError"
    assert "private" not in camera.failure_reason


def test_exclusive_release_failure_has_distinct_sanitized_flag():
    reading = threading.Event()

    class FailedExclusiveRelease(IdleCapture):
        def read(self):
            reading.set()
            return False, None

        def release(self):
            raise PermissionError("private device id must remain hidden")

    camera = Camera(
        0,
        mode="exclusive",
        capture_factory=FailedExclusiveRelease,
    )
    camera.start()
    assert reading.wait(1.0)

    with pytest.raises(RuntimeError):
        camera.stop()

    assert camera.release_failed is True
    assert camera.failure_reason == "PermissionError"
    assert camera.fatal_error == "PermissionError"
    assert "private" not in camera.failure_reason


def test_stuck_operation_is_fatal_but_not_a_release_failure():
    class StuckOpen(IdleCapture):
        def open(self):
            raise SharedCameraOperationStuckError("injected stuck operation")

    camera = Camera(0, mode="auto", shared_factory=lambda *_args: StuckOpen())
    camera.start()
    _eventually(lambda: camera.fatal_error == "SharedCameraOperationStuckError")

    with pytest.raises(RuntimeError):
        camera.stop()

    assert camera.release_failed is False
    assert camera.failure_reason == "SharedCameraOperationStuckError"


def test_unopened_dshow_release_failure_is_fatal_without_retry_or_reopen(monkeypatch):
    open_calls = []
    retry_calls = []

    class FailedUnopenedCapture:
        def isOpened(self):
            return False

        def release(self):
            raise OSError("private DirectShow details")

    monkeypatch.setattr("desk_sentry.camera._exclusive_capture", lambda *_args, **_kwargs: None)

    def open_dshow(*_args):
        open_calls.append(True)
        return FailedUnopenedCapture()

    monkeypatch.setattr(
        "desk_sentry.camera.cv2.VideoCapture",
        open_dshow,
    )

    def retry_wait(seconds):
        retry_calls.append(seconds)
        return True

    camera = Camera(
        0,
        mode="exclusive",
        retry_wait=retry_wait,
    )
    camera.start()
    _eventually(lambda: not camera.worker_alive)

    with pytest.raises(RuntimeError):
        camera.stop()

    assert camera.release_failed is True
    assert camera.failure_reason == "OSError"
    assert camera.fatal_error == "OSError"
    assert open_calls == [True]
    assert retry_calls == []


def test_release_flag_starts_false_and_resets_when_monitoring_restarts():
    camera = Camera(
        0,
        mode="exclusive",
        capture_factory=lambda: None,
        retry_wait=lambda _seconds: True,
    )
    assert camera.release_failed is False
    camera.release_failed = True

    camera.start()
    _eventually(lambda: not camera.worker_alive)
    camera.stop()

    assert camera.release_failed is False
