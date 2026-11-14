"""WinRT shared-read-only camera backend.

Device ``index`` follows the order returned by WinRT's video-capture device
list.  Windows does not guarantee that this order matches OpenCV/DirectShow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]


@dataclass(frozen=True)
class CameraInfo:
    index: int
    name: str
    id: str = ""


class CameraSourceUnavailableError(RuntimeError):
    """The selected video device does not expose a COLOR frame source."""


class CameraDisconnectedError(RuntimeError):
    """The saved camera identity is not present in the WinRT device list."""


class SharedCameraOperationStuckError(TimeoutError):
    """Cancellation did not finish, so fallback could race the same device."""

    camera_fatal = True


def _operation_result(operation: Any, timeout: float, types: dict[str, Any]) -> Any:
    status = operation.wait(timeout)
    if status == types["AsyncStatus"].STARTED:
        operation.cancel()
        if operation.wait(timeout) == types["AsyncStatus"].STARTED:
            raise SharedCameraOperationStuckError(
                "WinRT camera operation did not stop after cancellation"
            )
        raise TimeoutError("WinRT camera operation timed out")
    return operation.get()


def _color_device_ids(groups: list[Any], color_kind: Any) -> set[str]:
    """Return COLOR IDs, or an empty set to disable incomplete identity filtering."""
    ids: set[str] = set()
    for group in groups:
        for source_info in getattr(group, "source_infos", ()):
            if getattr(source_info, "source_kind", None) != color_kind:
                continue
            device = getattr(source_info, "device_information", None)
            identifier = getattr(device, "id", None)
            if not identifier or not str(identifier).strip():
                return set()
            ids.add(str(identifier).casefold())
    return ids


def _enumerate(types: dict[str, Any], timeout: float) -> tuple[list[Any], list[Any]]:
    groups = list(
        _operation_result(types["MediaFrameSourceGroup"].find_all_async(), timeout, types)
    )
    device_type = types.get("DeviceInformation")
    device_class = types.get("DeviceClass")
    if device_type is None or device_class is None:
        return [], groups
    devices = list(
        _operation_result(device_type.find_all_async(device_class.VIDEO_CAPTURE), timeout, types)
    )
    return devices, groups


def sole_camera_matches(camera_id: str, camera_name: str, timeout: float = 3.0) -> bool:
    """Return whether the only WinRT video device matches the saved identity."""

    try:
        types = _winrt_types()
        device_type = types.get("DeviceInformation")
        device_class = types.get("DeviceClass")
        if device_type is None or device_class is None:
            return False
        devices = list(
            _operation_result(
                device_type.find_all_async(device_class.VIDEO_CAPTURE),
                timeout,
                types,
            )
        )
        if len(devices) != 1:
            return False
        device = devices[0]
        actual_id = str(getattr(device, "id", ""))
        actual_name = str(getattr(device, "name", ""))
        return bool(
            (camera_id and actual_id.casefold() == camera_id.casefold())
            or (camera_name and actual_name == camera_name)
        )
    except Exception:
        return False


def list_cameras(timeout: float = 3.0) -> list[CameraInfo]:
    """Filter video devices by COLOR only when the source identities are complete."""

    try:
        types = _winrt_types()
        devices, groups = _enumerate(types, timeout)
        color_kind = types["MediaFrameSourceKind"].COLOR
        color_ids = _color_device_ids(groups, color_kind)
        return [
            CameraInfo(
                index,
                str(getattr(device, "name", "")),
                str(getattr(device, "id", "")),
            )
            for index, device in enumerate(devices)
            if not color_ids or str(getattr(device, "id", "")).casefold() in color_ids
        ]
    except Exception:
        return []


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
        camera_name: str = "",
        camera_id: str = "",
    ) -> None:
        if sharing_mode not in ("shared", "exclusive"):
            raise ValueError("sharing_mode must be 'shared' or 'exclusive'")
        self.index = index
        self.camera_name = camera_name
        self.camera_id = camera_id
        self._size = (width, height)
        self._operation_timeout = operation_timeout
        self._sharing_mode = sharing_mode
        self._capture: Any | None = None
        self._reader: Any | None = None
        self._types: dict[str, Any] | None = None
        self.subtype = ""
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self._source_fps = 0.0

    def open(self) -> None:
        types = _winrt_types()
        self._types = types
        devices, groups = _enumerate(types, self._operation_timeout)
        color_kind = types["MediaFrameSourceKind"].COLOR
        color_ids = _color_device_ids(groups, color_kind)
        selected = next(
            (
                device
                for device in devices
                if self.camera_id
                and str(getattr(device, "id", "")).casefold() == self.camera_id.casefold()
            ),
            None,
        )
        if selected is None and self.camera_name:
            named = [
                (position, device)
                for position, device in enumerate(devices)
                if getattr(device, "name", None) == self.camera_name
            ]
            if named:
                _, selected = min(named, key=lambda item: (abs(item[0] - self.index), item[0]))
        if selected is None:
            if self.camera_id or self.camera_name:
                raise CameraDisconnectedError("選擇的攝影機未連接")
            if self.index < 0 or self.index >= len(devices):
                raise IndexError("camera index is outside the WinRT device list")
            selected = devices[self.index]
        device_id = str(getattr(selected, "id", ""))
        if groups and color_ids and device_id.casefold() not in color_ids:
            raise CameraSourceUnavailableError(
                "selected camera does not expose a COLOR frame source"
            )

        settings = types["MediaCaptureInitializationSettings"]()
        settings.video_device_id = device_id
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
                (
                    source
                    for source in capture.frame_sources.values()
                    if source.info.source_kind == types["MediaFrameSourceKind"].COLOR
                ),
                None,
            )
            if source is None:
                raise CameraSourceUnavailableError(
                    "selected camera does not expose a COLOR frame source"
                )
            # Converted BGRA8 frames may carry a 0/0 rate; the source's native
            # format still declares what the driver delivers.
            try:
                self._source_fps = self._nominal_fps(source.current_format)
            except Exception:  # noqa: BLE001 - diagnostics only; never block capture
                self._source_fps = 0.0
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
        return _operation_result(operation, self._operation_timeout, types)

    def read(self) -> tuple[bool, Image | None]:
        reader = self._reader
        types = self._types
        if reader is None or types is None:
            return False, None
        reference = reader.try_acquire_latest_frame()
        if reference is None:
            return False, None
        self.fps = 0.0
        source_bitmap = None
        try:
            frame_format = getattr(reference, "format", None)
            # Read the declared rate before choosing a decode path: the normal
            # BGRA8 reader returns early but Windows still reports the rate.
            self.fps = self._nominal_fps(frame_format) or self._source_fps
            video = getattr(reference, "video_media_frame", None)
            source_bitmap = video.software_bitmap if video is not None else None
            if (
                source_bitmap is not None
                and source_bitmap.bitmap_pixel_format == types["BitmapPixelFormat"].BGRA8
            ):
                self.subtype = "BGRA8"
                self.width = int(source_bitmap.pixel_width)
                self.height = int(source_bitmap.pixel_height)
                return self._bitmap_frame(source_bitmap, types)

            data = self._frame_bytes(reference)
            if frame_format is not None and data is not None:
                subtype = str(frame_format.subtype).upper()
                video_format = frame_format.video_format
                width, height = int(video_format.width), int(video_format.height)
                self.subtype = subtype
                self.width, self.height = width, height
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
    def _nominal_fps(frame_format: Any) -> float:
        """Return the frame rate Windows declares for a frame, or 0.0 when unknown."""

        try:
            frame_rate = getattr(frame_format, "frame_rate", None)
            numerator = float(getattr(frame_rate, "numerator", 0) or 0)
            denominator = float(getattr(frame_rate, "denominator", 0) or 0)
        except (TypeError, ValueError, OSError):
            return 0.0
        if denominator <= 0 or numerator <= 0:
            return 0.0
        rate = numerator / denominator
        return rate if math.isfinite(rate) else 0.0

    @staticmethod
    def _nv12_frame(data: NDArray[np.uint8], width: int, height: int) -> tuple[bool, Image | None]:
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            return False, None
        aligned_width = ((width + 63) // 64) * 64
        aligned_heights = (
            height,
            ((height + 15) // 16) * 16,
            ((height + 31) // 32) * 32,
        )
        candidates = [(width, value) for value in aligned_heights]
        candidates.extend((aligned_width, value) for value in aligned_heights)
        for stride, aligned_height in dict.fromkeys(candidates):
            expected = stride * aligned_height * 3 // 2
            if data.size != expected:
                continue
            y_plane = data[: stride * aligned_height].reshape(aligned_height, stride)
            uv_plane = data[stride * aligned_height :].reshape(aligned_height // 2, stride)
            tight = np.vstack((y_plane[:height, :width], uv_plane[: height // 2, :width]))
            return True, np.asarray(cv2.cvtColor(tight, cv2.COLOR_YUV2BGR_NV12), np.uint8)
        return False, None

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
                stride = ((width + 63) // 64) * 64
                heights = (
                    height,
                    ((height + 15) // 16) * 16,
                    ((height + 31) // 32) * 32,
                )
                sizes = sorted(
                    {width * value * 3 // 2 for value in heights}
                    | {stride * value * 3 // 2 for value in heights}
                )
            else:
                size = width * height * 2
            if pixel_format == nv12:
                buffer = None
                for size in sizes:
                    candidate = types["Buffer"](size)
                    candidate.length = candidate.capacity
                    try:
                        bitmap.copy_to_buffer(candidate)
                    except (ValueError, OSError, RuntimeError):
                        continue
                    buffer = candidate
                    break
                if buffer is None:
                    return False, None
            else:
                buffer = types["Buffer"](size)
                buffer.length = buffer.capacity
                bitmap.copy_to_buffer(buffer)
            actual_length = int(buffer.length)
            data = np.frombuffer(memoryview(buffer), dtype=np.uint8, count=actual_length)
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
        MediaFrameSourceGroup,
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
        "MediaFrameSourceGroup": MediaFrameSourceGroup,
        "MediaFrameSourceKind": MediaFrameSourceKind,
        "MediaEncodingSubtypes": MediaEncodingSubtypes,
        "Buffer": Buffer,
    }
