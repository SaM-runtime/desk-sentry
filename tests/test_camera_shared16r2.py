"""TASK-16r2 partial WinRT COLOR metadata regressions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import desk_sentry.camera_shared as camera_shared
from desk_sentry.camera_shared import CameraInfo, SharedCameraCapture, list_cameras


class Done:
    def __init__(self, value=None):
        self.value = value

    def wait(self, _timeout):
        return 1

    def get(self):
        return self.value


def _types(devices, source_infos, *, actual_source_kind=1):
    settings = SimpleNamespace()
    reader = SimpleNamespace(
        acquisition_mode=None,
        start_async=lambda: Done(0),
        stop_async=lambda: Done(),
        close=lambda: None,
    )
    capture = SimpleNamespace(
        frame_sources={
            "source": SimpleNamespace(info=SimpleNamespace(source_kind=actual_source_kind))
        },
        initialize_async=lambda actual: (setattr(settings, "actual", actual), Done())[1],
        create_frame_reader_async=lambda *_args: Done(reader),
        close=lambda: None,
    )
    return (
        {
            "AsyncStatus": SimpleNamespace(STARTED=0),
            "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
            "DeviceInformation": SimpleNamespace(find_all_async=lambda _kind: Done(devices)),
            "MediaFrameSourceGroup": SimpleNamespace(
                find_all_async=lambda: Done([SimpleNamespace(source_infos=source_infos)])
            ),
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
        settings,
    )


@pytest.mark.parametrize("missing_first", [False, True])
def test_partial_color_device_ids_list_all_and_missing_id_device_opens(monkeypatch, missing_first):
    devices = [
        SimpleNamespace(id="DEVICE-A", name="Camera A"),
        SimpleNamespace(id="DEVICE-B", name="Camera B"),
    ]
    identified = SimpleNamespace(
        source_kind=1,
        device_information=SimpleNamespace(id="device-a"),
    )
    missing = SimpleNamespace(source_kind=1, device_information=None)
    source_infos = [missing, identified] if missing_first else [identified, missing]
    types, settings = _types(devices, source_infos)
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    assert list_cameras() == [
        CameraInfo(0, "Camera A", "DEVICE-A"),
        CameraInfo(1, "Camera B", "DEVICE-B"),
    ]
    selected = SharedCameraCapture(1, camera_id="device-b")
    selected.open()
    selected.release()
    assert settings.actual.video_device_id == "DEVICE-B"


def test_complete_color_device_ids_keep_case_insensitive_filter(monkeypatch):
    devices = [
        SimpleNamespace(id="DEVICE-A", name="Camera A"),
        SimpleNamespace(id="DEVICE-B", name="Camera B"),
    ]
    source_infos = [
        SimpleNamespace(
            source_kind=1,
            device_information=SimpleNamespace(id="device-a"),
        )
    ]
    types, _settings = _types(devices, source_infos)
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    assert list_cameras() == [CameraInfo(0, "Camera A", "DEVICE-A")]
    with pytest.raises(camera_shared.CameraSourceUnavailableError):
        SharedCameraCapture(1, camera_id="device-b").open()


def test_partial_ids_still_reject_selected_capture_without_actual_color_source(
    monkeypatch,
):
    devices = [
        SimpleNamespace(id="device-a", name="Camera A"),
        SimpleNamespace(id="device-b", name="Camera B"),
    ]
    source_infos = [
        SimpleNamespace(
            source_kind=1,
            device_information=SimpleNamespace(id="device-a"),
        ),
        SimpleNamespace(source_kind=1, device_information=None),
    ]
    types, _settings = _types(devices, source_infos, actual_source_kind=2)
    monkeypatch.setattr(camera_shared, "_winrt_types", lambda: types)

    with pytest.raises(camera_shared.CameraSourceUnavailableError):
        SharedCameraCapture(1, camera_id="DEVICE-B").open()
