"""Native WinRT reader fallback and per-frame decoding, without a camera."""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from desk_sentry.camera_shared import SharedCameraCapture


class Done:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def wait(self, _timeout):
        return 1

    def cancel(self):
        raise AssertionError("completed fake operation must not be cancelled")


class FakeBuffer:
    def __init__(self, value):
        if isinstance(value, int):
            self.data = bytearray(value)
        else:
            self.data = bytearray(value)
        self.capacity = len(self.data)
        self.length = len(self.data)

    def __buffer__(self, _flags):
        return memoryview(self.data)


class FakeBitmap:
    def __init__(self, pixel_format, width, height, data):
        self.bitmap_pixel_format = pixel_format
        self.pixel_width = width
        self.pixel_height = height
        self.data = bytes(data)
        self.closed = False

    def copy_to_buffer(self, buffer):
        buffer.data[:] = self.data

    def close(self):
        self.closed = True


class FakeReference:
    def __init__(self, subtype, width, height, *, data=b"", bitmap=None):
        self.format = SimpleNamespace(
            subtype=subtype,
            video_format=SimpleNamespace(width=width, height=height),
        )
        self.buffer_media_frame = SimpleNamespace(buffer=FakeBuffer(data)) if data else None
        self.video_media_frame = (
            SimpleNamespace(software_bitmap=bitmap) if bitmap is not None else None
        )
        self.closed = False

    def close(self):
        self.closed = True


class FakeReader:
    def __init__(self, status, references=()):
        self.status = status
        self.references = list(references)
        self.acquisition_mode = None
        self.stopped = False
        self.closed = False
        self.close_calls = 0

    def start_async(self):
        return Done(self.status)

    def try_acquire_latest_frame(self):
        return self.references.pop(0) if self.references else None

    def stop_async(self):
        self.stopped = True
        return Done()

    def close(self):
        self.closed = True
        self.close_calls += 1


class FakeCapture:
    def __init__(self, readers):
        self.source = SimpleNamespace(info=SimpleNamespace(source_kind=1))
        self.frame_sources = {"color": self.source}
        self.readers = list(readers)
        self.reader_args = []
        self.settings = None
        self.closed = False

    def initialize_async(self, settings):
        self.settings = settings
        return Done()

    def create_frame_reader_async(self, *args):
        self.reader_args.append(args)
        return Done(self.readers.pop(0))

    def close(self):
        self.closed = True


def install_types(monkeypatch, capture, *, convert=None):
    settings = SimpleNamespace()
    types = {
        "DeviceInformation": SimpleNamespace(
            find_all_async=lambda _kind: Done([SimpleNamespace(id="device-0")])
        ),
        "DeviceClass": SimpleNamespace(VIDEO_CAPTURE=4),
        "AsyncStatus": SimpleNamespace(STARTED=0),
        "MediaCaptureInitializationSettings": lambda: settings,
        "MediaCaptureSharingMode": SimpleNamespace(SHARED_READ_ONLY=1, EXCLUSIVE_CONTROL=0),
        "StreamingCaptureMode": SimpleNamespace(VIDEO=2),
        "MediaCaptureMemoryPreference": SimpleNamespace(CPU=1),
        "MediaCapture": lambda: capture,
        "MediaFrameSourceKind": SimpleNamespace(COLOR=1),
        "MediaEncodingSubtypes": SimpleNamespace(bgra8="Bgra8"),
        "MediaFrameReaderAcquisitionMode": SimpleNamespace(REALTIME=0),
        "MediaFrameReaderStartStatus": SimpleNamespace(SUCCESS=0),
        "BitmapPixelFormat": SimpleNamespace(BGRA8=87, NV12=103, YUY2=107),
        "BitmapAlphaMode": SimpleNamespace(IGNORE=2),
        "SoftwareBitmap": SimpleNamespace(convert=convert or (lambda *_args: None)),
        "Buffer": FakeBuffer,
    }
    monkeypatch.setattr("desk_sentry.camera_shared._winrt_types", lambda: types)
    return settings


def test_non_success_bgra_reader_closes_then_native_reader_decodes_real_mjpg(monkeypatch):
    original = np.full((6, 8, 3), (20, 80, 180), np.uint8)
    encoded_ok, encoded = cv2.imencode(".jpg", original)
    assert encoded_ok
    reference = FakeReference("MJPG", 8, 6, data=encoded.tobytes())
    rejected = FakeReader(4)
    native = FakeReader(0, [reference])
    capture = FakeCapture([rejected, native])
    install_types(monkeypatch, capture)

    shared = SharedCameraCapture(0, 8, 6)
    shared.open()
    ok, frame = shared.read()
    shared.release()

    assert len(capture.reader_args[0]) == 3
    assert capture.reader_args[1] == (capture.source,)
    assert rejected.closed and not rejected.stopped
    assert native.acquisition_mode == 0
    assert ok and frame is not None and frame.shape == original.shape
    assert np.abs(frame.astype(int) - original.astype(int)).mean() < 5
    assert reference.closed
    assert native.stopped and native.closed and capture.closed


def test_native_reader_decodes_format_and_size_changes_per_frame(monkeypatch):
    nv12 = bytes([82] * 16 + [90, 240] * 4)
    yuy2 = bytes([82, 90, 82, 240] * 2)
    bgra = bytes((1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255))
    rgb32 = bytes((10, 20, 30, 0, 40, 50, 60, 0))
    references = [
        FakeReference("NV12", 4, 4, data=nv12),
        FakeReference("YUY2", 2, 2, data=yuy2),
        FakeReference("Bgra8", 3, 1, data=bgra),
        FakeReference("Rgb32", 2, 1, data=rgb32),
    ]
    rejected = FakeReader(3)
    native = FakeReader(0, references)
    capture = FakeCapture([rejected, native])
    install_types(monkeypatch, capture)
    shared = SharedCameraCapture(0)
    shared.open()

    frames = [shared.read() for _ in references]
    shared.release()

    assert [frame.shape for ok, frame in frames if ok and frame is not None] == [
        (4, 4, 3),
        (2, 2, 3),
        (1, 3, 3),
        (1, 2, 3),
    ]
    for index in (0, 1):
        assert frames[index][1] is not None
        blue, green, red = frames[index][1][0, 0]
        assert int(blue) < 10 and int(green) < 10 and int(red) > 240
    assert frames[2][1] is not None and frames[2][1][0, 0].tolist() == [1, 2, 3]
    assert frames[3][1] is not None and frames[3][1][0, 1].tolist() == [40, 50, 60]
    assert all(reference.closed for reference in references)


def test_bitmap_only_frames_use_actual_pixel_format_over_reference_subtype(monkeypatch):
    bgra = FakeBitmap(87, 2, 1, bytes((1, 2, 3, 255, 4, 5, 6, 255)))
    nv12 = FakeBitmap(103, 4, 4, bytes([82] * 16 + [90, 240] * 4))
    yuy2 = FakeBitmap(107, 2, 2, bytes([82, 90, 82, 240] * 2))
    references = [
        FakeReference("NV12", 2, 1, bitmap=bgra),
        FakeReference("NV12", 4, 4, bitmap=nv12),
        FakeReference("YUY2", 2, 2, bitmap=yuy2),
    ]
    capture = FakeCapture([FakeReader(3), FakeReader(0, references)])
    install_types(monkeypatch, capture)
    shared = SharedCameraCapture(0)
    shared.open()

    frames = [shared.read() for _ in references]
    shared.release()

    assert frames[0][1] is not None and frames[0][1].tolist() == [[[1, 2, 3], [4, 5, 6]]]
    for index in (1, 2):
        assert frames[index][0] and frames[index][1] is not None
        blue, green, red = frames[index][1][0, 0]
        assert int(blue) < 10 and int(green) < 10 and int(red) > 240
    assert all(bitmap.closed for bitmap in (bgra, nv12, yuy2))
    assert all(reference.closed for reference in references)


def test_unknown_native_subtype_converts_bitmap_and_conversion_failure_is_no_frame(
    monkeypatch,
):
    first_source = FakeBitmap(99, 2, 1, b"unused")
    converted = FakeBitmap(87, 2, 1, bytes((1, 2, 3, 255, 4, 5, 6, 255)))
    second_source = FakeBitmap(99, 1, 1, b"unused")

    def convert(source, pixel_format, alpha):
        assert pixel_format == 87 and alpha == 2
        if source is first_source:
            return converted
        raise RuntimeError("injected unsupported conversion")

    references = [
        FakeReference("I420", 2, 1, bitmap=first_source),
        FakeReference("I420", 1, 1, bitmap=second_source),
    ]
    capture = FakeCapture([FakeReader(3), FakeReader(0, references)])
    install_types(monkeypatch, capture, convert=convert)
    shared = SharedCameraCapture(0)
    shared.open()

    first_ok, first = shared.read()
    second_ok, second = shared.read()
    shared.release()

    assert first_ok and first is not None and first.tolist() == [[[1, 2, 3], [4, 5, 6]]]
    assert (second_ok, second) == (False, None)
    assert first_source.closed and converted.closed and second_source.closed
    assert all(reference.closed for reference in references)


@pytest.mark.parametrize(("mode", "expected"), [("shared", 1), ("exclusive", 0)])
def test_sharing_mode_selects_capture_control_without_setting_source_format(
    monkeypatch, mode, expected
):
    reader = FakeReader(0)
    capture = FakeCapture([reader])
    settings = install_types(monkeypatch, capture)

    shared = SharedCameraCapture(0, sharing_mode=mode)
    shared.open()
    shared.release()

    assert settings.sharing_mode == expected
    assert len(capture.reader_args) == 1
    assert not hasattr(capture.source, "set_format_async")


def test_both_reader_start_failures_close_readers_and_capture(monkeypatch):
    converted = FakeReader(3)
    native = FakeReader(4)
    capture = FakeCapture([converted, native])
    install_types(monkeypatch, capture)

    with pytest.raises(RuntimeError, match="start status 4"):
        SharedCameraCapture(0).open()

    assert converted.closed and native.closed and capture.closed


def test_native_reader_creation_error_does_not_double_close_rejected_reader(monkeypatch):
    rejected = FakeReader(3)

    class FailedNativeCapture(FakeCapture):
        def create_frame_reader_async(self, *args):
            if self.reader_args:
                raise RuntimeError("injected native reader creation failure")
            return super().create_frame_reader_async(*args)

    capture = FailedNativeCapture([rejected])
    install_types(monkeypatch, capture)

    with pytest.raises(RuntimeError, match="native reader creation failure"):
        SharedCameraCapture(0).open()

    assert rejected.close_calls == 1
    assert capture.closed
