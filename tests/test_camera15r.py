"""TASK-15r camera identity and metadata regressions."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

import desk_sentry.camera_shared as camera_shared
from desk_sentry.camera import Camera
from desk_sentry.camera_shared import CameraInfo, list_cameras


class Done:
    def __init__(self, value=None):
        self.value = value

    def wait(self, _timeout):
        return 1

    def get(self):
        return self.value


def test_camera_info_keeps_old_constructor_and_lists_private_id(monkeypatch):
    old = CameraInfo(3, "Legacy Camera")
    assert (old.index, old.name, old.id) == (3, "Legacy Camera", "")

    devices = [SimpleNamespace(id="device-private-id", name="USB Camera")]
    groups = [
        SimpleNamespace(
            id="group-0",
            source_infos=[
                SimpleNamespace(
                    source_kind=1,
                    device_information=SimpleNamespace(id="device-private-id"),
                )
            ],
        )
    ]
    monkeypatch.setattr(
        "desk_sentry.camera_shared._winrt_types",
        lambda: {
            "AsyncStatus": SimpleNamespace(STARTED=0),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
            "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done(groups)),
            "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
        },
    )

    assert list_cameras() == [CameraInfo(0, "USB Camera", "device-private-id")]


def _install_open_types(monkeypatch, devices):
    settings = SimpleNamespace()
    reader = SimpleNamespace(
        acquisition_mode=None,
        start_async=lambda: Done(0),
        stop_async=lambda: Done(),
        close=lambda: None,
    )
    capture = SimpleNamespace(
        frame_sources={"color": SimpleNamespace(info=SimpleNamespace(source_kind=1))},
        initialize_async=lambda actual: (setattr(settings, "actual", actual), Done())[1],
        create_frame_reader_async=lambda *_args: Done(reader),
        close=lambda: None,
    )
    groups = [
        SimpleNamespace(
            id=f"group-{device.id}",
            source_infos=[
                SimpleNamespace(
                    source_kind=1,
                    device_information=SimpleNamespace(id=device.id),
                )
            ],
        )
        for device in devices
    ]
    monkeypatch.setattr(
        "desk_sentry.camera_shared._winrt_types",
        lambda: {
            "AsyncStatus": SimpleNamespace(STARTED=0),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
            "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done(groups)),
            "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
            "MediaCaptureInitializationSettings": lambda: settings,
            "MediaCaptureSharingMode": SimpleNamespace(SHARED_READ_ONLY=1, EXCLUSIVE_CONTROL=0),
            "StreamingCaptureMode": SimpleNamespace(VIDEO=2),
            "MediaCaptureMemoryPreference": SimpleNamespace(CPU=3),
            "MediaCapture": lambda: capture,
            "MediaEncodingSubtypes": SimpleNamespace(bgra8="Bgra8"),
            "MediaFrameReaderAcquisitionMode": SimpleNamespace(REALTIME=0),
            "MediaFrameReaderStartStatus": SimpleNamespace(SUCCESS=0),
        },
    )
    return settings


def test_open_uses_id_then_nearest_same_name_then_bare_index(monkeypatch):
    devices = [
        SimpleNamespace(id="usb-a", name="USB Camera"),
        SimpleNamespace(id="other", name="Other Camera"),
        SimpleNamespace(id="usb-b", name="USB Camera"),
    ]
    settings = _install_open_types(monkeypatch, devices)

    by_id = camera_shared.SharedCameraCapture(2, camera_id="usb-a", camera_name="USB Camera")
    by_id.open()
    by_id.release()
    assert settings.actual.video_device_id == "usb-a"

    by_nearest_name = camera_shared.SharedCameraCapture(2, camera_name="USB Camera")
    by_nearest_name.open()
    by_nearest_name.release()
    assert settings.actual.video_device_id == "usb-b"

    by_index = camera_shared.SharedCameraCapture(1)
    by_index.open()
    by_index.release()
    assert settings.actual.video_device_id == "other"


def test_saved_camera_missing_raises_disconnected_instead_of_using_index(monkeypatch):
    devices = [SimpleNamespace(id="other", name="Other Camera")]
    _install_open_types(monkeypatch, devices)

    with pytest.raises(RuntimeError, match="未連接") as caught:
        camera_shared.SharedCameraCapture(
            0,
            camera_id="missing-id",
            camera_name="Missing Camera",
        ).open()

    assert type(caught.value).__name__ == "CameraDisconnectedError"


@pytest.mark.parametrize(
    ("camera_id", "camera_name"),
    [("saved-private-id", ""), ("", "Saved Camera")],
)
def test_missing_saved_identity_never_uses_dshow_index(monkeypatch, camera_id, camera_name):
    opened = []

    def disconnected_winrt(index, width, height, **kwargs):
        opened.append((index, width, height, kwargs))
        raise camera_shared.CameraDisconnectedError("選擇的攝影機未連接")

    monkeypatch.setattr("desk_sentry.camera._exclusive_capture", disconnected_winrt)
    camera = Camera(
        4,
        camera_id=camera_id,
        camera_name=camera_name,
        mode="exclusive",
        retry_wait=lambda _seconds: True,
        sole_camera_matcher=lambda *_args: False,
    )
    monkeypatch.setattr(
        camera, "_open_dshow", lambda _index: pytest.fail("DSHOW must not open by index")
    )

    camera.start()
    deadline = time.monotonic() + 1
    while camera.worker_alive and time.monotonic() < deadline:
        time.sleep(0.005)
    camera.stop()

    assert opened == [
        (
            4,
            1280,
            720,
            {
                "camera_id": camera_id,
                "camera_name": camera_name,
                "sharing_mode": "exclusive",
            },
        )
    ]
    assert camera.failure_reason == "CameraDisconnectedError"
    assert "saved-private-id" not in camera.failure_reason
    assert "Saved Camera" not in camera.failure_reason


def test_bgra_early_return_sets_format_metadata():
    pixels = bytes((10, 20, 30, 255, 40, 50, 60, 255))

    class Buffer(bytearray):
        def __new__(cls, capacity):
            return super().__new__(cls, capacity)

        def __init__(self, capacity):
            super().__init__(capacity)
            self.capacity = capacity
            self.length = capacity

    class Bitmap:
        bitmap_pixel_format = 87
        pixel_width = 2
        pixel_height = 1

        def copy_to_buffer(self, target):
            target[:] = pixels

        def close(self):
            return None

    class Reference:
        video_media_frame = SimpleNamespace(software_bitmap=Bitmap())
        format = None
        buffer_media_frame = None

        def close(self):
            return None

    capture = camera_shared.SharedCameraCapture(0)
    capture._types = {
        "BitmapPixelFormat": SimpleNamespace(BGRA8=87, NV12=103, YUY2=107),
        "BitmapAlphaMode": SimpleNamespace(IGNORE=2),
        "SoftwareBitmap": SimpleNamespace(convert=lambda *_args: None),
        "Buffer": Buffer,
    }
    capture._reader = SimpleNamespace(try_acquire_latest_frame=Reference)

    ok, frame = capture.read()

    assert ok and frame is not None and frame.shape == (1, 2, 3)
    assert (capture.subtype, capture.width, capture.height) == ("BGRA8", 2, 1)


def test_camera_uses_frame_shape_when_capture_metadata_is_zero():
    class ZeroMetadataCapture:
        subtype = "BGRA8"
        width = 0
        height = 0
        fps = 0.0

        def read(self):
            time.sleep(0.002)
            return True, np.zeros((3, 5, 3), np.uint8)

        def release(self):
            return None

    camera = Camera(0, capture_factory=ZeroMetadataCapture)
    camera.start()
    try:
        seq, frame = camera.latest(0, 1)
        assert seq and frame is not None
        assert (camera.subtype, camera.width, camera.height) == ("BGRA8", 5, 3)
    finally:
        camera.stop()
