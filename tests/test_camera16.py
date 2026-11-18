"""TASK-16 camera fallback, metadata, and identity regressions."""

from __future__ import annotations

import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import desk_sentry.camera_shared as camera_shared
from desk_sentry.camera import Camera
from desk_sentry.camera_shared import CameraInfo, SharedCameraCapture, list_cameras


class Done:
    def __init__(self, value=None):
        self.value = value

    def wait(self, _timeout):
        return 1

    def get(self):
        return self.value


class ClosedCapture:
    def open(self):
        raise OSError("injected WinRT failure")

    def read(self):
        return False, None

    def release(self):
        return None


class DshowCapture:
    def __init__(self):
        self.released = False

    def read(self):
        return False, None

    def release(self):
        self.released = True


def _install_enumeration(monkeypatch, devices):
    monkeypatch.setattr(
        camera_shared,
        "_winrt_types",
        lambda: {
            "AsyncStatus": SimpleNamespace(STARTED=0),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
            "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done([])),
        },
    )


def _wait_for_worker(camera: Camera) -> None:
    deadline = time.monotonic() + 1.0
    while camera.worker_alive and time.monotonic() < deadline:
        time.sleep(0.002)
    camera.stop()


@pytest.mark.parametrize(
    ("camera_id", "camera_name", "matcher_result", "expected_dshow"),
    [
        ("SAVED-ID", "", True, [0]),
        ("SAVED-ID", "", False, []),
        ("different", "Different camera", False, []),
        ("", "", False, [4]),
    ],
)
def test_saved_identity_uses_dshow_zero_only_for_sole_match(
    monkeypatch, camera_id, camera_name, matcher_result, expected_dshow
):
    monkeypatch.setattr(
        "desk_sentry.camera._exclusive_capture", lambda *_args, **_kwargs: ClosedCapture()
    )
    dshow_indices = []
    camera = Camera(
        4,
        camera_id=camera_id,
        camera_name=camera_name,
        mode="exclusive",
        retry_wait=lambda _seconds: True,
        sole_camera_matcher=lambda *_args: matcher_result,
    )
    monkeypatch.setattr(
        camera,
        "_open_dshow",
        lambda index: (dshow_indices.append(index), DshowCapture())[1],
    )

    camera.start()
    _wait_for_worker(camera)

    assert dshow_indices == expected_dshow


def test_saved_identity_matcher_exception_disables_dshow(monkeypatch):
    monkeypatch.setattr(
        "desk_sentry.camera._exclusive_capture", lambda *_args, **_kwargs: ClosedCapture()
    )
    camera = Camera(
        0,
        camera_id="saved-id",
        mode="exclusive",
        retry_wait=lambda _seconds: True,
        sole_camera_matcher=lambda *_args: (_ for _ in ()).throw(OSError("enumeration failed")),
    )
    monkeypatch.setattr(
        camera, "_open_dshow", lambda _index: pytest.fail("DSHOW must stay disabled")
    )

    camera.start()
    _wait_for_worker(camera)


@pytest.mark.parametrize("mode", ["auto", "exclusive"])
@pytest.mark.parametrize(
    ("devices", "camera_id", "camera_name", "expected_dshow"),
    [
        ([SimpleNamespace(id="DEVICE-A", name="Camera A")], "device-a", "", [0]),
        (
            [
                SimpleNamespace(id="device-a", name="Camera A"),
                SimpleNamespace(id="device-b", name="Camera B"),
            ],
            "device-a",
            "",
            [],
        ),
        ([SimpleNamespace(id="device-a", name="Camera A")], "other", "Other", []),
    ],
)
def test_real_sole_device_query_gates_auto_and_exclusive_dshow(
    monkeypatch, mode, devices, camera_id, camera_name, expected_dshow
):
    _install_enumeration(monkeypatch, devices)
    monkeypatch.setattr("desk_sentry.camera._STALL_SECONDS", 0.0)
    monkeypatch.setattr(
        "desk_sentry.camera._exclusive_capture",
        lambda *_args, **_kwargs: ClosedCapture(),
    )
    dshow_indices = []
    camera = Camera(
        3,
        camera_id=camera_id,
        camera_name=camera_name,
        mode=mode,
        shared_factory=lambda *_args: ClosedCapture(),
        retry_wait=lambda _seconds: True,
    )
    monkeypatch.setattr(
        camera,
        "_open_dshow",
        lambda index: (dshow_indices.append(index), DshowCapture())[1],
    )

    camera.start()
    _wait_for_worker(camera)

    assert dshow_indices == expected_dshow


def test_injected_capture_factory_does_not_enumerate_devices():
    camera = Camera(
        2,
        camera_id="saved-id",
        mode="exclusive",
        capture_factory=DshowCapture,
        retry_wait=lambda _seconds: True,
        sole_camera_matcher=lambda *_args: pytest.fail(
            "an injected capture factory must not enumerate devices"
        ),
    )

    camera.start()
    _wait_for_worker(camera)


@pytest.mark.parametrize(
    ("devices", "camera_id", "camera_name", "expected"),
    [
        ([SimpleNamespace(id="DEVICE-A", name="Camera A")], "device-a", "", True),
        ([SimpleNamespace(id="device-a", name="Camera A")], "", "Camera A", True),
        (
            [
                SimpleNamespace(id="device-a", name="Camera A"),
                SimpleNamespace(id="device-b", name="Camera B"),
            ],
            "device-a",
            "",
            False,
        ),
        ([SimpleNamespace(id="device-a", name="Camera A")], "other", "Other", False),
    ],
)
def test_sole_camera_matches_all_video_capture_devices(
    monkeypatch, devices, camera_id, camera_name, expected
):
    monkeypatch.setattr(
        camera_shared,
        "_winrt_types",
        lambda: {
            "AsyncStatus": SimpleNamespace(STARTED=0),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
            "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done([])),
        },
    )

    assert camera_shared.sole_camera_matches(camera_id, camera_name, 0.1) is expected


def test_sole_camera_matches_returns_false_on_enumeration_error(monkeypatch):
    monkeypatch.setattr(
        camera_shared,
        "_winrt_types",
        lambda: (_ for _ in ()).throw(ImportError("WinRT unavailable")),
    )
    assert camera_shared.sole_camera_matches("saved-id", "", 0.1) is False


def test_native_declared_fps_does_not_replace_measured_fps():
    camera = Camera(0, capture_factory=lambda: None)
    camera.fps = 12.5
    frame = np.zeros((2, 3, 3), np.uint8)
    native = SimpleNamespace(subtype="MJPG", width=3, height=2, fps=30.0)

    camera._update_metadata(native, frame)

    assert camera.fps == 12.5
    assert camera.nominal_fps == 30.0


def test_native_frame_loop_reports_measured_and_nominal_fps():
    class NativeCapture:
        subtype = "MJPG"
        width = 4
        height = 2
        fps = 30.0

        def open(self):
            return None

        def read(self):
            time.sleep(0.001)
            return True, np.zeros((2, 4, 3), np.uint8)

        def release(self):
            return None

    ticks = iter((0.0, 0.4, 1.2))
    camera = Camera(
        0,
        mode="auto",
        shared_factory=lambda *_args: NativeCapture(),
        clock=lambda: next(ticks, 1.2),
    )
    camera.start()
    deadline = time.monotonic() + 1.0
    sequence = 0
    try:
        while camera.fps == 0 and time.monotonic() < deadline:
            sequence, _frame = camera.latest(sequence, 0.05)
        assert sequence >= 2
        assert camera.fps == pytest.approx(2 / 1.2)
        assert camera.nominal_fps == 30.0
    finally:
        camera.stop()


def test_winrt_to_opencv_resets_metadata_before_factory_and_decodes_fourcc(monkeypatch):
    monkeypatch.setattr("desk_sentry.camera._STALL_SECONDS", 0.0)
    camera = None

    class FailedWinrt:
        def open(self):
            assert camera is not None
            camera.subtype = "NV12"
            camera.width = 1920
            camera.height = 1080
            camera.nominal_fps = 60.0
            raise OSError("injected WinRT failure")

        def read(self):
            return False, None

        def release(self):
            return None

    class OpenCvCapture:
        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            return (True, np.zeros((4, 5, 3), np.uint8)) if self.reads == 1 else (False, None)

        def get(self, prop):
            values = {
                cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter.fourcc(*"MJPG")),
                cv2.CAP_PROP_FPS: 25.0,
            }
            return values.get(prop, 0.0)

        def release(self):
            return None

    monkeypatch.setattr(
        "desk_sentry.camera._exclusive_capture", lambda *_args, **_kwargs: FailedWinrt()
    )
    camera = Camera(
        0,
        camera_id="device-a",
        mode="exclusive",
        retry_wait=lambda _seconds: True,
        sole_camera_matcher=lambda *_args: True,
    )

    def open_dshow(index):
        assert index == 0
        assert (camera.subtype, camera.width, camera.height, camera.nominal_fps) == (
            "",
            0,
            0,
            0.0,
        )
        return OpenCvCapture()

    monkeypatch.setattr(camera, "_open_dshow", open_dshow)
    camera.start()
    _wait_for_worker(camera)

    assert (camera.subtype, camera.width, camera.height) == ("MJPG", 5, 4)
    assert camera.nominal_fps == 25.0


def test_connection_metadata_resets_and_opencv_fourcc_is_decoded():
    camera = Camera(0, capture_factory=lambda: None)
    camera.subtype = "NV12"
    camera.width = 1920
    camera.height = 1080
    camera.nominal_fps = 60.0
    camera._reset_connection_metadata()

    class OpenCvCapture:
        def get(self, prop):
            values = {
                cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter.fourcc(*"MJPG")),
                cv2.CAP_PROP_FPS: 25.0,
            }
            return values.get(prop, 0.0)

    camera._update_metadata(OpenCvCapture(), np.zeros((4, 5, 3), np.uint8))

    assert (camera.subtype, camera.width, camera.height) == ("MJPG", 5, 4)
    assert camera.nominal_fps == 25.0


@pytest.mark.parametrize(
    "fourcc",
    [
        1.0,
        float("nan"),
        float("inf"),
        float(2**32 + cv2.VideoWriter.fourcc(*"MJPG")),
    ],
)
def test_invalid_opencv_fourcc_is_empty(fourcc):
    camera = Camera(0, capture_factory=lambda: None)

    class InvalidFourccCapture:
        def get(self, prop):
            return fourcc if prop == cv2.CAP_PROP_FOURCC else 0.0

    camera._update_metadata(InvalidFourccCapture(), np.zeros((1, 1, 3), np.uint8))

    assert camera.subtype == ""


@pytest.mark.parametrize("nominal", [0.0, -1.0, float("nan"), float("inf")])
def test_unknown_nominal_fps_is_zero(nominal):
    camera = Camera(0, capture_factory=lambda: None)
    camera._update_metadata(
        SimpleNamespace(subtype="MJPG", width=1, height=1, fps=nominal),
        np.zeros((1, 1, 3), np.uint8),
    )
    assert camera.nominal_fps == 0.0


def _enumeration_types(devices, groups, *, with_capture=False):
    types = {
        "AsyncStatus": SimpleNamespace(STARTED=0),
        "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
        "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
        "MediaFrameSourceGroup": SimpleNamespace(find_all_async=lambda: Done(groups)),
        "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
    }
    if not with_capture:
        return types
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
    types.update(
        {
            "MediaCaptureInitializationSettings": lambda: settings,
            "MediaCaptureSharingMode": SimpleNamespace(SHARED_READ_ONLY=1, EXCLUSIVE_CONTROL=0),
            "StreamingCaptureMode": SimpleNamespace(VIDEO=2),
            "MediaCaptureMemoryPreference": SimpleNamespace(CPU=3),
            "MediaCapture": lambda: capture,
            "MediaEncodingSubtypes": SimpleNamespace(bgra8="Bgra8"),
            "MediaFrameReaderAcquisitionMode": SimpleNamespace(REALTIME=0),
            "MediaFrameReaderStartStatus": SimpleNamespace(SUCCESS=0),
        }
    )
    return types, settings


def test_device_ids_are_case_insensitive_for_listing_and_open(monkeypatch):
    devices = [SimpleNamespace(id="DEVICE-A", name="Camera A")]
    groups = [
        SimpleNamespace(
            source_infos=[
                SimpleNamespace(
                    source_kind=1,
                    device_information=SimpleNamespace(id="device-a"),
                )
            ]
        )
    ]
    types, settings = _enumeration_types(devices, groups, with_capture=True)
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    assert list_cameras() == [CameraInfo(0, "Camera A", "DEVICE-A")]
    capture = SharedCameraCapture(0, camera_id="device-a")
    capture.open()
    capture.release()
    assert settings.actual.video_device_id == "DEVICE-A"


def test_missing_source_device_ids_do_not_hide_video_devices(monkeypatch):
    devices = [SimpleNamespace(id="device-a", name="Camera A")]
    groups = [
        SimpleNamespace(source_infos=[SimpleNamespace(source_kind=1, device_information=None)])
    ]
    types, settings = _enumeration_types(devices, groups, with_capture=True)
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    assert list_cameras() == [CameraInfo(0, "Camera A", "device-a")]
    capture = SharedCameraCapture(0, camera_id="DEVICE-A")
    capture.open()
    capture.release()
    assert settings.actual.video_device_id == "device-a"


def test_missing_source_device_ids_still_requires_actual_color_frame_source(monkeypatch):
    devices = [SimpleNamespace(id="device-a", name="Camera A")]
    groups = [
        SimpleNamespace(source_infos=[SimpleNamespace(source_kind=1, device_information=None)])
    ]
    types, _settings = _enumeration_types(devices, groups, with_capture=True)
    types["MediaCapture"]().frame_sources = {
        "depth": SimpleNamespace(info=SimpleNamespace(source_kind=2))
    }
    capture = types["MediaCapture"]()
    capture.frame_sources = {"depth": SimpleNamespace(info=SimpleNamespace(source_kind=2))}
    types["MediaCapture"] = lambda: capture
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    with pytest.raises(camera_shared.CameraSourceUnavailableError):
        SharedCameraCapture(0).open()
