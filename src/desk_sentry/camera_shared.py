"""WinRT shared-read-only camera backend.

Device ``index`` follows the order returned by WinRT's video-capture device
list.  Windows does not guarantee that this order matches OpenCV/DirectShow.
"""

from __future__ import annotations

from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]


class SharedCameraOperationStuckError(TimeoutError):
    """Cancellation did not finish, so fallback could race the same device."""

    camera_fatal = True


def bgra_to_bgr(data: bytes | bytearray | memoryview, *, width: int, height: int) -> Image:
    """Copy tightly packed BGRA8 bytes into an OpenCV-compatible BGR image."""

    expected = width * height * 4
    pixels = np.frombuffer(data, dtype=np.uint8, count=expected)
    if pixels.size != expected:
        raise ValueError("BGRA8 frame has an unexpected buffer size")
    return np.ascontiguousarray(pixels.reshape(height, width, 4)[..., :3])


class SharedCameraCapture:
    """Small synchronous facade over WinRT's asynchronous MediaFrameReader."""

    def __init__(
        self,
        index: int,
        width: int = 1280,
        height: int = 720,
        *,
        operation_timeout: float = 3.0,
        sharing_mode: Literal["shared", "exclusive"] = "shared",
    ) -> None:
        if sharing_mode not in ("shared", "exclusive"):
            raise ValueError("sharing_mode must be 'shared' or 'exclusive'")
        self.index = index
        self._size = (width, height)
        self._operation_timeout = operation_timeout
        self._sharing_mode = sharing_mode
        self._capture: Any | None = None
        self._reader: Any | None = None
        self._types: dict[str, Any] | None = None

    def open(self) -> None:
        types = _winrt_types()
        self._types = types
        devices = self._get(
            types["DeviceInformation"].find_all_async(types["DeviceClass"].VIDEO_CAPTURE)
        )
        if self.index < 0 or self.index >= len(devices):
            raise IndexError("camera index is outside the WinRT device list")

        settings = types["MediaCaptureInitializationSettings"]()
        settings.video_device_id = devices[self.index].id
        settings.sharing_mode = (
            types["MediaCaptureSharingMode"].SHARED_READ_ONLY
            if self._sharing_mode == "shared"
            else types["MediaCaptureSharingMode"].EXCLUSIVE_CONTROL
        )
        settings.streaming_capture_mode = types["StreamingCaptureMode"].VIDEO
        settings.memory_preference = types["MediaCaptureMemoryPreference"].CPU

        capture = types["MediaCapture"]()
        reader = None
        try:
            self._get(capture.initialize_async(settings))
            source = next(
                source
                for source in capture.frame_sources.values()
                if source.info.source_kind == types["MediaFrameSourceKind"].COLOR
            )
            reader = self._get(
                capture.create_frame_reader_async(
                    source,
                    types["MediaEncodingSubtypes"].bgra8,
                    self._size,
                )
            )
            reader.acquisition_mode = types["MediaFrameReaderAcquisitionMode"].REALTIME
            status = self._get(reader.start_async())
            if status != types["MediaFrameReaderStartStatus"].SUCCESS:
                reader.close()
                reader = None
                reader = self._get(capture.create_frame_reader_async(source))
                reader.acquisition_mode = types["MediaFrameReaderAcquisitionMode"].REALTIME
                status = self._get(reader.start_async())
                if status != types["MediaFrameReaderStartStatus"].SUCCESS:
                    failed_status = int(status)
                    reader.close()
                    reader = None
                    raise RuntimeError(f"MediaFrameReader start status {failed_status}")
        except Exception:
            if reader is not None:
                reader.close()
            capture.close()
            raise

        self._capture = capture
        self._reader = reader

    def _get(self, operation: Any) -> Any:
        types = self._types
        if types is None:
            raise RuntimeError("WinRT types are not loaded")
        status = operation.wait(self._operation_timeout)
        if status == types["AsyncStatus"].STARTED:
            operation.cancel()
            if operation.wait(self._operation_timeout) == types["AsyncStatus"].STARTED:
                raise SharedCameraOperationStuckError(
                    "WinRT camera operation did not stop after cancellation"
                )
            raise TimeoutError("WinRT camera operation timed out")
        return operation.get()

    def read(self) -> tuple[bool, Image | None]:
        reader = self._reader
        types = self._types
        if reader is None or types is None:
            return False, None
        reference = reader.try_acquire_latest_frame()
        if reference is None:
            return False, None
        source_bitmap = None
        try:
            video = getattr(reference, "video_media_frame", None)
            source_bitmap = video.software_bitmap if video is not None else None
            if (
                source_bitmap is not None
                and source_bitmap.bitmap_pixel_format == types["BitmapPixelFormat"].BGRA8
            ):
                return self._bitmap_frame(source_bitmap, types)

            frame_format = getattr(reference, "format", None)
            data = self._frame_bytes(reference)
            if frame_format is not None and data is not None:
                subtype = str(frame_format.subtype).upper()
                video_format = frame_format.video_format
                width, height = int(video_format.width), int(video_format.height)
                if subtype in ("MJPG", "MJPEG"):
                    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
                    if frame is None:
                        return False, None
                    return True, np.ascontiguousarray(frame, dtype=np.uint8)
                if subtype == "NV12":
                    return self._nv12_frame(data, width, height)
                if subtype in ("YUY2", "YUYV"):
                    return self._yuy2_frame(data, width, height)
                if subtype in ("BGRA8", "RGB32"):
                    expected = width * height * 4
                    if data.size >= expected:
                        return True, bgra_to_bgr(
                            memoryview(data[:expected]), width=width, height=height
                        )

            if source_bitmap is not None:
                return self._bitmap_frame(source_bitmap, types)
            return False, None
        except (ValueError, cv2.error):
            return False, None
        finally:
            if source_bitmap is not None:
                source_bitmap.close()
            reference.close()

    @staticmethod
    def _frame_bytes(reference: Any) -> NDArray[np.uint8] | None:
        buffer_frame = getattr(reference, "buffer_media_frame", None)
        if buffer_frame is None or buffer_frame.buffer is None:
            return None
        buffer = buffer_frame.buffer
        length = int(buffer.length)
        return np.frombuffer(memoryview(buffer), dtype=np.uint8, count=length)

    @staticmethod
    def _nv12_frame(data: NDArray[np.uint8], width: int, height: int) -> tuple[bool, Image | None]:
        expected = width * height * 3 // 2
        if data.size < expected:
            return False, None
        nv12_data = data[:expected].reshape(height * 3 // 2, width)
        return True, np.asarray(cv2.cvtColor(nv12_data, cv2.COLOR_YUV2BGR_NV12), np.uint8)

    @staticmethod
    def _yuy2_frame(data: NDArray[np.uint8], width: int, height: int) -> tuple[bool, Image | None]:
        expected = width * height * 2
        if data.size < expected:
            return False, None
        yuy2_data = data[:expected].reshape(height, width, 2)
        return True, np.asarray(cv2.cvtColor(yuy2_data, cv2.COLOR_YUV2BGR_YUY2), np.uint8)

    @staticmethod
    def _bitmap_frame(bitmap: Any, types: dict[str, Any]) -> tuple[bool, Image | None]:
        converted = None
        try:
            pixel_format = bitmap.bitmap_pixel_format
            formats = types["BitmapPixelFormat"]
            bgra8 = formats.BGRA8
            nv12 = formats.NV12
            yuy2 = formats.YUY2
            if pixel_format not in (bgra8, nv12, yuy2):
                try:
                    converted = types["SoftwareBitmap"].convert(
                        bitmap,
                        bgra8,
                        types["BitmapAlphaMode"].IGNORE,
                    )
                except Exception:
                    return False, None
                bitmap = converted
                pixel_format = bitmap.bitmap_pixel_format

            width, height = int(bitmap.pixel_width), int(bitmap.pixel_height)
            if pixel_format == bgra8:
                size = width * height * 4
            elif pixel_format == nv12:
                size = width * height * 3 // 2
            else:
                size = width * height * 2
            buffer = types["Buffer"](size)
            buffer.length = buffer.capacity
            bitmap.copy_to_buffer(buffer)
            data = np.frombuffer(memoryview(buffer), dtype=np.uint8, count=size)
            if pixel_format == nv12:
                return SharedCameraCapture._nv12_frame(data, width, height)
            if pixel_format == yuy2:
                return SharedCameraCapture._yuy2_frame(data, width, height)
            return True, bgra_to_bgr(memoryview(data), width=width, height=height)
        finally:
            if converted is not None:
                converted.close()

    def release(self) -> None:
        reader, self._reader = self._reader, None
        capture, self._capture = self._capture, None
        first_error: Exception | None = None
        if reader is not None:
            try:
                self._get(reader.stop_async())
            except Exception as exc:  # release all resources before reporting
                first_error = exc
            try:
                reader.close()
            except Exception as exc:
                first_error = first_error or exc
        if capture is not None:
            try:
                capture.close()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


def _winrt_types() -> dict[str, Any]:
    """Import optional WinRT projections only when the shared backend is opened."""

    from winrt.windows.devices.enumeration import DeviceClass, DeviceInformation
    from winrt.windows.foundation import AsyncStatus
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.media.capture import (
        MediaCapture,
        MediaCaptureInitializationSettings,
        MediaCaptureMemoryPreference,
        MediaCaptureSharingMode,
        StreamingCaptureMode,
    )
    from winrt.windows.media.capture.frames import (
        MediaFrameReaderAcquisitionMode,
        MediaFrameReaderStartStatus,
        MediaFrameSourceKind,
    )
    from winrt.windows.media.mediaproperties import MediaEncodingSubtypes
    from winrt.windows.storage.streams import Buffer

    return {
        "DeviceClass": DeviceClass,
        "DeviceInformation": DeviceInformation,
        "AsyncStatus": AsyncStatus,
        "BitmapAlphaMode": BitmapAlphaMode,
        "BitmapPixelFormat": BitmapPixelFormat,
        "SoftwareBitmap": SoftwareBitmap,
        "MediaCapture": MediaCapture,
        "MediaCaptureInitializationSettings": MediaCaptureInitializationSettings,
        "MediaCaptureMemoryPreference": MediaCaptureMemoryPreference,
        "MediaCaptureSharingMode": MediaCaptureSharingMode,
        "StreamingCaptureMode": StreamingCaptureMode,
        "MediaFrameReaderAcquisitionMode": MediaFrameReaderAcquisitionMode,
        "MediaFrameReaderStartStatus": MediaFrameReaderStartStatus,
        "MediaFrameSourceKind": MediaFrameSourceKind,
        "MediaEncodingSubtypes": MediaEncodingSubtypes,
        "Buffer": Buffer,
    }
