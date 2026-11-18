"""Nominal frame rate is read before the BGRA8 early return (review-mwenqa2c P2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from desk_sentry import camera_shared


def _capture_for(frame_format: object) -> camera_shared.SharedCameraCapture:
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
        format = frame_format
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
    return capture


def test_bgra8_bitmap_path_reports_declared_frame_rate():
    frame_format = SimpleNamespace(frame_rate=SimpleNamespace(numerator=30000, denominator=1001))
    capture = _capture_for(frame_format)

    ok, frame = capture.read()

    assert ok and frame is not None
    assert capture.subtype == "BGRA8"
    assert capture.fps == pytest.approx(29.97, abs=0.01)


@pytest.mark.parametrize(
    "frame_format",
    [
        None,
        SimpleNamespace(frame_rate=None),
        SimpleNamespace(frame_rate=SimpleNamespace(numerator=30, denominator=0)),
        SimpleNamespace(frame_rate=SimpleNamespace(numerator=0, denominator=1)),
        SimpleNamespace(frame_rate=SimpleNamespace(numerator="bad", denominator=1)),
    ],
)
def test_bgra8_bitmap_path_unknown_frame_rate_is_zero(frame_format):
    capture = _capture_for(frame_format)

    ok, frame = capture.read()

    assert ok and frame is not None
    assert capture.fps == 0.0


def test_bgra8_zero_frame_rate_falls_back_to_source_format_rate():
    # Real hardware (2026-11-13): converted BGRA8 frames report 0/0 while the
    # COLOR source's native NV12 format declares 30/1.
    frame_format = SimpleNamespace(frame_rate=SimpleNamespace(numerator=0, denominator=0))
    capture = _capture_for(frame_format)
    capture._source_fps = 30.0

    ok, frame = capture.read()

    assert ok and frame is not None
    assert capture.fps == 30.0


def test_frame_declared_rate_wins_over_source_rate():
    frame_format = SimpleNamespace(frame_rate=SimpleNamespace(numerator=15, denominator=1))
    capture = _capture_for(frame_format)
    capture._source_fps = 30.0

    capture.read()

    assert capture.fps == 15.0
