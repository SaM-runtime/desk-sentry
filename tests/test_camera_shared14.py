"""Shared-camera behavior without opening a physical camera."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from desk_sentry.camera import Camera
from desk_sentry.camera_shared import (
    SharedCameraCapture,
    SharedCameraOperationStuckError,
    bgra_to_bgr,
)


class FakeCapture:
    def __init__(self, reads=(), *, open_error: Exception | None = None) -> None:
        self.reads = iter(reads)
        self.open_error = open_error
        self.opened = False
        self.released = False

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.opened = True

    def read(self):
        return next(self.reads, (False, None))

    def release(self) -> None:
        self.released = True


class FakeExclusive(FakeCapture):
    def isOpened(self) -> bool:
        return True

    def set(self, _prop, _value) -> bool:
        return True


def _eventually(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_capture_factory_keeps_legacy_tests_on_exclusive_backend():
    frame = np.full((2, 3, 3), 7, np.uint8)
    cap = FakeExclusive([(True, frame)])
    camera = Camera(0, capture_factory=lambda: cap)

    camera.start()
    try:
        _eventually(lambda: camera.backend_name == "exclusive")
        seq, actual = camera.latest(0, 0.5)
        assert seq == 1
        assert np.array_equal(actual, frame)
        assert camera.failure_reason is None
    finally:
        camera.stop()


def test_auto_prefers_shared_and_preserves_public_latest_contract():
    frame = np.full((4, 5, 3), 11, np.uint8)
    shared = FakeCapture([(True, frame)])
    exclusive_calls = []
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: shared,
        capture_factory=lambda: exclusive_calls.append(True),
        mode="auto",
    )

    camera.start()
    try:
        seq, actual = camera.latest(0, 0.5)
        assert seq == 1
        assert np.array_equal(actual, frame)
        assert camera.backend_name == "shared"
        assert camera.failure_reason is None
        assert exclusive_calls == []
    finally:
        camera.stop()
    assert shared.released


def test_auto_falls_back_to_exclusive_and_exposes_only_exception_type():
    shared = FakeCapture(open_error=PermissionError("private device path must not escape"))
    frame = np.zeros((2, 2, 3), np.uint8)
    exclusive = FakeExclusive([(True, frame)])
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: shared,
        capture_factory=lambda: exclusive,
        mode="auto",
    )

    camera.start()
    try:
        _eventually(lambda: camera.backend_name == "exclusive")
        assert camera.failure_reason == "PermissionError"
        assert "private" not in camera.failure_reason
    finally:
        camera.stop()


def test_auto_falls_back_when_shared_produces_no_frames():
    ticks = iter((0.0, 0.0, 0.25, 0.5, 0.75, 1.1, 1.2, 1.3))
    shared = FakeCapture()
    exclusive = FakeExclusive([(True, np.zeros((1, 1, 3), np.uint8))])
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: shared,
        capture_factory=lambda: exclusive,
        mode="auto",
        shared_timeout=1.0,
        clock=lambda: next(ticks, 2.0),
    )

    camera.start()
    try:
        _eventually(lambda: camera.backend_name == "exclusive")
        assert camera.failure_reason == "TimeoutError"
    finally:
        camera.stop()
    assert shared.released


def test_bgra_conversion_accepts_resolution_changes():
    first = bgra_to_bgr(bytes(range(24)), width=3, height=2)
    second = bgra_to_bgr(bytes(range(16)), width=2, height=2)

    assert first.shape == (2, 3, 3)
    assert second.shape == (2, 2, 3)
    assert first[0, 0].tolist() == [0, 1, 2]
    assert second[-1, -1].tolist() == [12, 13, 14]


def test_winrt_adapter_requests_shared_cpu_video_and_returns_bgr(monkeypatch):
    class Done:
        def __init__(self, value=None):
            self.value = value

        def get(self):
            return self.value

        def wait(self, _timeout):
            return 1

        def cancel(self):
            raise AssertionError("completed fake operation must not be cancelled")

    class Buffer:
        def __init__(self, capacity):
            self.data = bytearray(capacity)
            self.capacity = capacity
            self.length = 0

        def __buffer__(self, _flags):
            return memoryview(self.data)

    class Bitmap:
        def __init__(self, pixel_format, width, height, data):
            self.bitmap_pixel_format = pixel_format
            self.pixel_width = width
            self.pixel_height = height
            self.data = data
            self.closed = False

        def copy_to_buffer(self, buffer):
            buffer.data[:] = self.data

        def close(self):
            self.closed = True

    first_bitmap = Bitmap(99, 8, 6, b"not-used-after-convert")
    converted = Bitmap(
        87,
        3,
        1,
        bytes((1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255)),
    )
    second_bitmap = Bitmap(
        87,
        2,
        2,
        bytes(range(16)),
    )
    all_references = [
        SimpleNamespace(
            video_media_frame=SimpleNamespace(software_bitmap=bitmap),
            closed=False,
            close=lambda ref=None: None,
        )
        for bitmap in (first_bitmap, second_bitmap)
    ]
    for reference in all_references:
        reference.close = lambda reference=reference: setattr(reference, "closed", True)
    references = list(all_references)

    class Reader:
        acquisition_mode = None
        closed = stopped = False

        def start_async(self):
            return Done(0)

        def try_acquire_latest_frame(self):
            return references.pop(0)

        def stop_async(self):
            self.stopped = True
            return Done()

        def close(self):
            self.closed = True

    reader = Reader()
    source = SimpleNamespace(info=SimpleNamespace(source_kind=1))

    class Capture:
        def __init__(self):
            self.frame_sources = {"color": source}
            self.settings = None
            self.closed = False

        def initialize_async(self, settings):
            self.settings = settings
            return Done()

        def create_frame_reader_async(self, actual_source, subtype, size):
            assert actual_source is source
            assert subtype == "Bgra8"
            assert size == (320, 240)
            return Done(reader)

        def close(self):
            self.closed = True

    capture = Capture()
    settings = SimpleNamespace()
    types = {
        "DeviceInformation": SimpleNamespace(
            find_all_async=lambda _kind: Done([SimpleNamespace(id="device-0", name="USB Camera")])
        ),
        "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
        "AsyncStatus": SimpleNamespace(STARTED=0),
        "MediaFrameSourceGroup": SimpleNamespace(
            find_all_async=lambda: Done(
                [
                    SimpleNamespace(
                        id="group-0",
                        source_infos=[
                            SimpleNamespace(
                                source_kind=1,
                                device_information=SimpleNamespace(id="device-0"),
                            )
                        ],
                    )
                ]
            )
        ),
        "MediaCaptureInitializationSettings": lambda: settings,
        "MediaCaptureSharingMode": SimpleNamespace(SHARED_READ_ONLY=1),
        "StreamingCaptureMode": SimpleNamespace(VIDEO=2),
        "MediaCaptureMemoryPreference": SimpleNamespace(CPU=1),
        "MediaCapture": lambda: capture,
        "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
        "MediaEncodingSubtypes": SimpleNamespace(bgra8="Bgra8"),
        "MediaFrameReaderAcquisitionMode": SimpleNamespace(REALTIME=0),
        "MediaFrameReaderStartStatus": SimpleNamespace(SUCCESS=0),
        "BitmapPixelFormat": SimpleNamespace(BGRA8=87, NV12=103, YUY2=107),
        "BitmapAlphaMode": SimpleNamespace(IGNORE=2),
        "SoftwareBitmap": SimpleNamespace(
            convert=lambda source_bitmap, pixel_format, alpha: (
                converted
                if source_bitmap is first_bitmap and pixel_format == 87 and alpha == 2
                else pytest.fail("unexpected SoftwareBitmap conversion")
            )
        ),
        "Buffer": Buffer,
    }
    monkeypatch.setattr("desk_sentry.camera_shared._winrt_types", lambda: types)

    shared = SharedCameraCapture(0, 320, 240)
    shared.open()
    ok_first, first_frame = shared.read()
    first_reference_closed = all_references[0].closed
    ok_second, second_frame = shared.read()
    shared.release()

    assert settings.video_device_id == "device-0"
    assert settings.sharing_mode == 1
    assert settings.streaming_capture_mode == 2
    assert settings.memory_preference == 1
    assert reader.acquisition_mode == 0
    assert ok_first and first_frame is not None
    assert first_frame.tolist() == [[[1, 2, 3], [4, 5, 6], [7, 8, 9]]]
    assert ok_second and second_frame is not None and second_frame.shape == (2, 2, 3)
    assert first_reference_closed and all(reference.closed for reference in all_references)
    assert not references
    assert first_bitmap.closed and converted.closed and second_bitmap.closed
    assert reader.stopped and reader.closed and capture.closed


def test_stop_does_not_acknowledge_before_shared_release_finishes():
    releasing = threading.Event()
    released = threading.Event()

    class BlockingRelease(FakeCapture):
        def release(self) -> None:
            releasing.set()
            released.wait(1.0)
            super().release()

    shared = BlockingRelease()
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: shared,
        mode="auto",
        shared_timeout=10.0,
    )
    camera.start()
    _eventually(lambda: camera.backend_name == "shared")

    completed = threading.Event()
    stopper = threading.Thread(target=lambda: (camera.stop(), completed.set()))
    stopper.start()
    assert releasing.wait(0.5)
    assert not completed.is_set()
    released.set()
    stopper.join(1.0)
    assert completed.is_set()


def test_stop_raises_instead_of_false_success_when_worker_is_stuck(monkeypatch):
    releasing = threading.Event()
    released = threading.Event()

    class BlockingRelease(FakeCapture):
        def release(self) -> None:
            releasing.set()
            released.wait(1.0)

    monkeypatch.setattr("desk_sentry.camera._STOP_TIMEOUT", 0.02)
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: BlockingRelease(),
        mode="auto",
        shared_timeout=10.0,
    )
    camera.start()
    _eventually(lambda: camera.backend_name == "shared")

    with pytest.raises(TimeoutError):
        camera.stop()
    assert releasing.is_set()
    released.set()
    camera.stop()


def test_exclusive_release_failure_is_sticky_and_stop_never_acknowledges():
    class FailedRelease(FakeExclusive):
        def release(self):
            self.released = True
            raise OSError("injected release failure")

    capture = FailedRelease()
    camera = Camera(0, capture_factory=lambda: capture)
    camera.start()
    _eventually(lambda: camera.backend_name == "exclusive")

    with pytest.raises(RuntimeError):
        camera.stop()
    assert capture.released
    assert camera.failure_reason == "OSError"
    with pytest.raises(RuntimeError):
        camera.stop()


def test_shared_initialization_timeout_cancels_before_returning(monkeypatch):
    class Completed:
        def wait(self, _timeout):
            return 1

        def get(self):
            return []

    class Pending:
        def __init__(self):
            self.cancelled = False

        def wait(self, _timeout):
            return 2 if self.cancelled else 0

        def cancel(self):
            self.cancelled = True

        def get(self):
            raise AssertionError("timed-out operation must not call get")

    pending = Pending()
    monkeypatch.setattr(
        "desk_sentry.camera_shared._winrt_types",
        lambda: {
            "MediaFrameSourceGroup": SimpleNamespace(find_all_async=Completed),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: pending),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "AsyncStatus": SimpleNamespace(STARTED=0),
        },
    )

    with pytest.raises(TimeoutError):
        SharedCameraCapture(0, operation_timeout=0.01).open()
    assert pending.cancelled


def test_uncancelled_shared_open_never_races_an_exclusive_fallback():
    shared = FakeCapture(open_error=SharedCameraOperationStuckError("injected stuck open"))
    exclusive_calls = []
    camera = Camera(
        0,
        shared_factory=lambda _index, _width, _height: shared,
        capture_factory=lambda: exclusive_calls.append(True),
    )
    camera.start()
    _eventually(lambda: camera.failure_reason == "SharedCameraOperationStuckError")

    with pytest.raises(RuntimeError):
        camera.stop()
    assert exclusive_calls == []


def test_probe_logic_is_injectable_and_never_needs_a_real_camera():
    probe = load_probe("camera_share_probe")

    class ProbeCamera:
        backend_name = "shared"
        failure_reason = None

        def __init__(self):
            self.seq = 0

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self, after_seq, _timeout):
            self.seq = after_seq + 1
            return self.seq, np.zeros((1, 1, 3), np.uint8)

    class ProbeCapture:
        def isOpened(self):
            return True

        def read(self):
            return True, np.zeros((1, 1, 3), np.uint8)

        def release(self):
            pass

    result = probe.run_probe(
        index=0,
        seconds=0.02,
        shared_factory=lambda _index: ProbeCamera(),
        cv_factory=lambda _index, _backend: ProbeCapture(),
        exclusive_factory=lambda _index: ProbeCapture(),
    )

    assert result["shared_backend"] == "shared"
    assert result["shared_frames"] > 0
    assert result["msmf_frames"] > 0
    assert result["dshow_frames"] > 0
    assert result["msmf_continuous"] is True
    assert result["shared_during_msmf_continuous"] is True
    assert result["dshow_continuous"] is True
    assert result["shared_during_dshow_continuous"] is True
    assert result["shareable"] is True


def test_probe_rejects_one_shared_frame_followed_by_a_stall():
    probe = load_probe("camera_share_probe_stalled")

    class TickClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 0.05
            return self.now

    class StalledSharedCamera:
        backend_name = "shared"
        failure_reason = None

        def __init__(self):
            self.calls = 0

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self, after_seq, _timeout):
            self.calls += 1
            if self.calls <= 2:  # warmup, then one frame beside MSMF
                return after_seq + 1, np.zeros((1, 1, 3), np.uint8)
            return after_seq, None

    class ContinuousPeer:
        def isOpened(self):
            return True

        def read(self):
            return True, np.zeros((1, 1, 3), np.uint8)

        def release(self):
            pass

    result = probe.run_probe(
        index=0,
        seconds=1.0,
        shared_factory=lambda _index: StalledSharedCamera(),
        cv_factory=lambda _index, _backend: ContinuousPeer(),
        exclusive_factory=lambda _index: ContinuousPeer(),
        clock=TickClock(),
    )

    assert result["msmf_continuous"] is True
    assert result["dshow_continuous"] is True
    assert result["shared_during_dshow_continuous"] is False
    assert result["shared_during_dshow_frames"] == 1
    assert result["shared_during_winrt_exclusive_continuous"] is False
    assert result["shared_during_msmf_continuous"] is False
    assert result["shareable"] is False


def test_probe_rejects_frames_after_camera_falls_back_to_exclusive():
    probe = load_probe("camera_share_probe_backend_switch")

    class TickClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 0.05
            return self.now

    class FallbackCamera:
        backend_name = "shared"
        failure_reason = None

        def __init__(self):
            self.calls = 0

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self, after_seq, _timeout):
            self.calls += 1
            if self.calls >= 2:
                self.backend_name = "exclusive"
                self.failure_reason = "TimeoutError"
            return after_seq + 1, np.zeros((1, 1, 3), np.uint8)

    class ContinuousPeer:
        def isOpened(self):
            return True

        def read(self):
            return True, np.zeros((1, 1, 3), np.uint8)

        def release(self):
            pass

    result = probe.run_probe(
        index=0,
        seconds=1.0,
        shared_factory=lambda _index: FallbackCamera(),
        cv_factory=lambda _index, _backend: ContinuousPeer(),
        exclusive_factory=lambda _index: ContinuousPeer(),
        clock=TickClock(),
    )

    assert result["shared_during_msmf_continuous"] is True
    assert result["shared_during_dshow_continuous"] is True
    assert result["shared_backend_after_msmf"] == "exclusive"
    assert result["shared_failure_after_msmf"] == "TimeoutError"
    assert result["shared_backend_final"] == "exclusive"
    assert result["shared_failure_final"] == "TimeoutError"
    assert result["shareable"] is False


def test_probe_default_camera_reports_shared_failure_without_opening_fallback():
    probe = load_probe("camera_share_probe_no_fallback")
    created = []

    class FailedSharedCapture:
        def open(self):
            raise PermissionError("injected shared failure")

        def read(self):
            return False, None

        def release(self):
            pass

    def make_shared(index, width, height):
        created.append((index, width, height))
        return FailedSharedCapture()

    result = probe.run_probe(
        index=4,
        seconds=0.01,
        shared_capture_factory=make_shared,
        cv_factory=lambda *_args: pytest.fail("peer must not open after shared failure"),
    )

    assert created == [(4, 1280, 720)]
    assert result["shared_backend"] == "exclusive"
    assert result["shared_failure"] == "PermissionError"
    assert result["shared_backend_final"] == "exclusive"
    assert result["shareable"] is False


def test_probe_reports_no_camera_without_opening_opencv():
    class NoCamera:
        backend_name = "exclusive"
        failure_reason = "IndexError"

        def start(self):
            pass

        def stop(self):
            pass

        def latest(self, after_seq, _timeout):
            return after_seq, None

    result = probe_run_no_camera(NoCamera)

    assert result["shared_backend"] == "exclusive"
    assert result["shared_failure"] == "IndexError"
    assert result["shareable"] is False


def probe_run_no_camera(no_camera_type):
    probe = load_probe("camera_share_probe_no_camera")
    return probe.run_probe(
        index=9,
        seconds=0.01,
        shared_factory=lambda _index: no_camera_type(),
        cv_factory=lambda *_args: pytest.fail("OpenCV must not open after shared failure"),
    )


def load_probe(name):
    path = Path(__file__).parents[1] / "tools" / "camera_share_probe.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe
