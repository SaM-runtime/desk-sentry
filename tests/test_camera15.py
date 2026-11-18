"""Task 15 camera selection, yielding, fallback detail, and NV12 padding."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from desk_sentry.camera import Camera
from desk_sentry.camera_shared import (
    CameraInfo,
    CameraSourceUnavailableError,
    SharedCameraCapture,
    list_cameras,
)


class Done:
    def __init__(self, value=None):
        self.value = value

    def wait(self, _timeout):
        return 1

    def get(self):
        return self.value


def group(name: str, identifier: str, *kinds: int):
    return SimpleNamespace(
        display_name=name,
        id=f"group-{identifier}",
        source_infos=[
            SimpleNamespace(
                source_kind=kind,
                device_information=SimpleNamespace(id=identifier),
            )
            for kind in kinds
        ],
    )


def group_types(groups):
    devices = [
        SimpleNamespace(
            id=item.source_infos[0].device_information.id,
            name=item.display_name,
        )
        for item in groups
    ]
    return {
        "AsyncStatus": SimpleNamespace(STARTED=0),
        "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
        "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
        "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done(groups)),
        "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
    }


def test_list_cameras_keeps_only_color_groups_and_returns_empty_on_failure(monkeypatch):
    groups = [group("USB Camera", "usb", 1), group("Depth only", "depth", 2)]
    monkeypatch.setattr("desk_sentry.camera_shared._winrt_types", lambda: group_types(groups))
    assert list_cameras() == [CameraInfo(0, "USB Camera", "usb")]

    monkeypatch.setattr(
        "desk_sentry.camera_shared._winrt_types",
        lambda: (_ for _ in ()).throw(ImportError("winrt absent")),
    )
    assert list_cameras() == []


def test_open_prefers_exact_camera_name_and_reports_group_without_color(monkeypatch):
    groups = [group("Built in", "built", 1), group("USB Camera", "usb", 1)]
    settings = SimpleNamespace()
    capture = SimpleNamespace(
        frame_sources={"color": SimpleNamespace(info=SimpleNamespace(source_kind=1))},
        initialize_async=lambda actual: (setattr(settings, "actual", actual), Done())[1],
        create_frame_reader_async=lambda *_args: Done(
            SimpleNamespace(
                acquisition_mode=None,
                start_async=lambda: Done(0),
                stop_async=lambda: Done(),
                close=lambda: None,
            )
        ),
        close=lambda: None,
    )
    types = group_types(groups) | {
        "MediaCaptureInitializationSettings": lambda: settings,
        "MediaCaptureSharingMode": SimpleNamespace(SHARED_READ_ONLY=1, EXCLUSIVE_CONTROL=0),
        "StreamingCaptureMode": SimpleNamespace(VIDEO=2),
        "MediaCaptureMemoryPreference": SimpleNamespace(CPU=3),
        "MediaCapture": lambda: capture,
        "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
        "MediaEncodingSubtypes": SimpleNamespace(bgra8="Bgra8"),
        "MediaFrameReaderAcquisitionMode": SimpleNamespace(REALTIME=0),
        "MediaFrameReaderStartStatus": SimpleNamespace(SUCCESS=0),
    }
    monkeypatch.setattr("desk_sentry.camera_shared._winrt_types", lambda: types)
    selected = SharedCameraCapture(0, camera_name="USB Camera")
    selected.open()
    selected.release()
    assert settings.actual.video_device_id == "usb"

    groups.append(group("Infrared", "ir", 2))
    types.update(group_types(groups))
    with pytest.raises(CameraSourceUnavailableError, match="COLOR"):
        SharedCameraCapture(0, camera_name="Infrared").open()


class Capture:
    def __init__(self, *, open_error: Exception | None = None, frame_value=7):
        self.open_error = open_error
        self.frame_value = frame_value
        self.released = False

    def open(self):
        if self.open_error:
            raise self.open_error

    def read(self):
        time.sleep(0.002)
        return True, np.full((2, 3, 3), self.frame_value, np.uint8)

    def release(self):
        self.released = True


def test_shared_failure_yields_without_opening_exclusive_then_recovers():
    allowed = threading.Event()
    waits = []
    exclusive_calls = []
    shared_calls = []

    def shared_factory(*_args):
        shared_calls.append(1)
        return Capture(open_error=PermissionError("shared unavailable"))

    def retry(seconds):
        waits.append(seconds)
        allowed.set()
        return False

    camera = Camera(
        0,
        shared_factory=shared_factory,
        capture_factory=lambda: exclusive_calls.append(1) or Capture(frame_value=11),
        exclusive_allowed=allowed.is_set,
        retry_wait=retry,
    )
    camera.start()
    try:
        deadline = time.monotonic() + 1
        while camera.backend_name != "exclusive" and time.monotonic() < deadline:
            time.sleep(0.005)
        seq, frame = camera.latest(0, 1)
        assert seq and frame is not None and frame[0, 0].tolist() == [11, 11, 11]
        assert waits[0] == 5
        assert len(shared_calls) >= 2
        assert exclusive_calls == [1]
    finally:
        camera.stop()


def test_default_exclusive_tries_winrt_same_name_before_dshow(monkeypatch):
    opened = []

    def winrt(index, width, height, *, camera_name, camera_id, sharing_mode):
        opened.append((index, width, height, camera_name, camera_id, sharing_mode))
        return Capture(frame_value=19)

    monkeypatch.setattr("desk_sentry.camera._exclusive_capture", winrt)
    camera = Camera(
        2,
        camera_name="USB Camera",
        mode="exclusive",
        sole_camera_matcher=lambda *_args: False,
    )
    monkeypatch.setattr(
        camera, "_open_dshow", lambda _index: pytest.fail("DSHOW fallback was unexpected")
    )
    camera.start()
    try:
        seq, frame = camera.latest(0, 1)
        assert seq and frame is not None and frame[0, 0].tolist() == [19, 19, 19]
        assert camera.backend_name == "exclusive" and camera.backend_detail == "winrt"
    finally:
        camera.stop()
    assert opened == [(2, 1280, 720, "USB Camera", "", "exclusive")]


def test_padded_nv12_matches_tight_color_and_bitmap_path():
    width, height, aligned_height = 1920, 1080, 1088
    y = 82
    uv = bytes((90, 240))
    tight = np.frombuffer(bytes([y]) * (width * height) + uv * (width * height // 4), np.uint8)
    padded = np.frombuffer(
        bytes([y]) * (width * aligned_height) + uv * (width * aligned_height // 4), np.uint8
    )
    ok_tight, expected = SharedCameraCapture._nv12_frame(tight, width, height)
    ok_padded, actual = SharedCameraCapture._nv12_frame(padded, width, height)
    assert ok_tight and ok_padded and expected is not None and actual is not None
    assert np.array_equal(actual, expected)

    class Buffer:
        def __init__(self, capacity):
            self.data = bytearray(capacity)
            self.capacity = capacity
            self.length = capacity

        def __buffer__(self, _flags):
            return memoryview(self.data)

    class Bitmap:
        bitmap_pixel_format = 103
        pixel_width = width
        pixel_height = height

        def copy_to_buffer(self, target):
            if target.capacity < padded.size:
                raise ValueError("buffer too small")
            target.data[: padded.size] = padded.tobytes()
            target.length = padded.size

    types = {
        "BitmapPixelFormat": SimpleNamespace(BGRA8=87, NV12=103, YUY2=107),
        "BitmapAlphaMode": SimpleNamespace(IGNORE=2),
        "SoftwareBitmap": SimpleNamespace(convert=lambda *_args: None),
        "Buffer": Buffer,
    }
    ok_bitmap, bitmap_frame = SharedCameraCapture._bitmap_frame(Bitmap(), types)
    assert ok_bitmap and bitmap_frame is not None and np.array_equal(bitmap_frame, expected)


def test_list_cameras_joins_devices_to_color_sources_by_device_id(monkeypatch):
    devices = [
        SimpleNamespace(id="depth-id", name="Depth Camera"),
        SimpleNamespace(id="usb-id", name="USB Camera"),
    ]
    groups = [
        SimpleNamespace(
            display_name="A display name that must not be used for the join",
            id="source-group-a",
            source_infos=[
                SimpleNamespace(
                    source_kind=7,
                    device_information=SimpleNamespace(id="usb-id"),
                )
            ],
        ),
        SimpleNamespace(
            display_name="USB Camera",
            id="source-group-b",
            source_infos=[
                SimpleNamespace(
                    source_kind=8,
                    device_information=SimpleNamespace(id="other-id"),
                )
            ],
        ),
    ]
    types = {
        "AsyncStatus": SimpleNamespace(STARTED=0),
        "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
        "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
        "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done(groups)),
        "MediaFrameSourceKind": SimpleNamespace(COLOR=7),
    }
    monkeypatch.setattr("desk_sentry.camera_shared._winrt_types", lambda: types)

    assert list_cameras(timeout=0.25) == [CameraInfo(index=1, name="USB Camera", id="usb-id")]


def test_stride_and_height_padded_nv12_bitmap_matches_tight_color():
    width, height = 1918, 1080
    stride, aligned_height = 1920, 1088
    y, u, v = 82, 90, 240
    tight = np.empty(width * height * 3 // 2, np.uint8)
    tight[: width * height] = y
    tight[width * height :] = np.tile([u, v], width * height // 4)
    padded = np.zeros(stride * aligned_height * 3 // 2, np.uint8)
    padded[: stride * aligned_height].reshape(aligned_height, stride)[:, :width] = y
    uv = padded[stride * aligned_height :].reshape(aligned_height // 2, stride)
    uv[: height // 2, :width] = np.tile([u, v], width * height // 4).reshape(height // 2, width)

    class Buffer:
        def __init__(self, capacity):
            self.data = bytearray(capacity)
            self.capacity = capacity
            self.length = capacity

        def __buffer__(self, _flags):
            return memoryview(self.data)

    class Bitmap:
        bitmap_pixel_format = 103
        pixel_width = width
        pixel_height = height

        def copy_to_buffer(self, target):
            if target.capacity < padded.size:
                raise ValueError("buffer too small")
            target.data[: padded.size] = padded.tobytes()
            target.length = padded.size

    types = {
        "BitmapPixelFormat": SimpleNamespace(BGRA8=87, NV12=103, YUY2=107),
        "BitmapAlphaMode": SimpleNamespace(IGNORE=2),
        "SoftwareBitmap": SimpleNamespace(convert=lambda *_args: None),
        "Buffer": Buffer,
    }
    ok_tight, expected = SharedCameraCapture._nv12_frame(tight, width, height)
    ok_bitmap, actual = SharedCameraCapture._bitmap_frame(Bitmap(), types)

    assert ok_tight and ok_bitmap and expected is not None and actual is not None
    assert np.array_equal(actual, expected)


def test_camera_exposes_public_worker_and_fatal_metadata():
    class FatalCapture:
        def open(self):
            return None

        def read(self):
            error = RuntimeError("stuck")
            error.camera_fatal = True  # type: ignore[attr-defined]
            raise error

        def release(self):
            return None

    camera = Camera(0, shared_factory=lambda *_args: FatalCapture())
    assert camera.fatal_error is None
    assert camera.worker_alive is False

    camera.start()
    deadline = time.monotonic() + 1
    while camera.worker_alive and time.monotonic() < deadline:
        time.sleep(0.005)

    assert camera.worker_alive is False
    assert camera.fatal_error == "RuntimeError"
    assert camera.subtype == ""
    assert camera.width == 0
    assert camera.height == 0
    assert isinstance(camera.fps, float)
    with pytest.raises(RuntimeError, match="camera worker failed"):
        camera.stop()


def test_exclusive_permission_is_rechecked_before_dshow_fallback(monkeypatch):
    allowed = threading.Event()
    allowed.set()
    waits = []

    def failing_winrt(*_args, **_kwargs):
        allowed.clear()
        raise PermissionError("WinRT exclusive unavailable")

    monkeypatch.setattr("desk_sentry.camera._exclusive_capture", failing_winrt)
    camera = Camera(
        0,
        mode="exclusive",
        exclusive_allowed=allowed.is_set,
        retry_wait=lambda seconds: waits.append(seconds) or True,
    )
    monkeypatch.setattr(camera, "_open", lambda: pytest.fail("DSHOW must remain closed"))

    camera.start()
    deadline = time.monotonic() + 1
    while camera.worker_alive and time.monotonic() < deadline:
        time.sleep(0.005)

    assert camera.backend_name == "yielded"
    assert waits == [5.0]
    camera.stop()


def test_auto_returns_to_shared_after_working_winrt_exclusive_is_lost(monkeypatch):
    shared_calls = []

    def shared_factory(*_args):
        shared_calls.append(1)
        if len(shared_calls) == 1:
            return Capture(open_error=PermissionError("shared busy"))
        return Capture(frame_value=23)

    class OneFrameCapture(Capture):
        def __init__(self):
            super().__init__(frame_value=17)
            self.reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, np.full((2, 3, 3), 17, np.uint8)
            return False, None

    now = 0.0

    def clock():
        nonlocal now
        now += 4.0
        return now

    monkeypatch.setattr(
        "desk_sentry.camera._exclusive_capture", lambda *_a, **_k: OneFrameCapture()
    )
    camera = Camera(0, shared_factory=shared_factory, clock=clock)
    monkeypatch.setattr(camera, "_open", lambda: pytest.fail("DSHOW fallback was unexpected"))

    camera.start()
    try:
        deadline = time.monotonic() + 1
        while (
            camera.backend_name != "shared" or len(shared_calls) < 2 or not camera.connected
        ) and time.monotonic() < deadline:
            time.sleep(0.005)
        seq, frame = camera.latest(1, 1)
        assert len(shared_calls) >= 2
        assert camera.backend_name == "shared"
        assert seq and frame is not None and frame[0, 0].tolist() == [23, 23, 23]
    finally:
        camera.stop()
