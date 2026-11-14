"""Background webcam reader that always exposes only the newest frame."""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable
from typing import Literal, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from desk_sentry.health import ReconnectBackoff

Image = NDArray[np.uint8]

_STALL_SECONDS = 3.0
_SHARED_TIMEOUT = 3.0
_STOP_TIMEOUT = 5.0


class SharedCapture(Protocol):
    def open(self) -> None: ...
    def read(self) -> tuple[bool, Image | None]: ...
    def release(self) -> None: ...


class Camera:
    """Reads continuously so the driver buffer never serves stale frames.

    Frames live only in RAM; nothing is written to disk.
    """

    def __init__(
        self,
        index: int,
        *,
        width: int = 1280,
        height: int = 720,
        capture_factory: Callable[[], cv2.VideoCapture | None] | None = None,
        retry_wait: Callable[[float], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        mode: Literal["auto", "exclusive"] = "auto",
        shared_factory: Callable[[int, int, int], SharedCapture] | None = None,
        shared_timeout: float = _SHARED_TIMEOUT,
        camera_name: str = "",
        camera_id: str = "",
        exclusive_allowed: Callable[[], bool] = lambda: True,
        sole_camera_matcher: Callable[[str, str, float], bool] | None = None,
    ) -> None:
        if mode not in ("auto", "exclusive"):
            raise ValueError("camera mode must be 'auto' or 'exclusive'")
        self.index = index
        self.camera_name = camera_name
        self.camera_id = camera_id
        self._size = (width, height)
        self._lock = threading.Condition()
        self._frame: Image | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._capture_factory = capture_factory or self._open
        self._injected_capture_factory = capture_factory is not None
        self._shared_factory = shared_factory or (
            lambda selected, frame_width, frame_height: _shared_capture(
                selected,
                frame_width,
                frame_height,
                camera_name=self.camera_name,
                camera_id=self.camera_id,
            )
        )
        # Existing injected OpenCV tests must not begin enumerating real devices.
        self._use_shared = mode == "auto" and (
            capture_factory is None or shared_factory is not None
        )
        self._shared_timeout = shared_timeout
        self._retry_wait = retry_wait or self._stop.wait
        self._clock = clock
        self._exclusive_allowed = exclusive_allowed
        self._sole_camera_matcher = sole_camera_matcher or _sole_camera_matches
        self.last_frame_at: float | None = None
        self._thread: threading.Thread | None = None
        self._worker_error: Exception | None = None
        self.connected = False
        self.fps = 0.0
        self.nominal_fps = 0.0
        self.backend_name: Literal["shared", "exclusive", "pending", "yielded"] = "pending"
        self.backend_detail = ""
        self.failure_reason: str | None = None
        self.subtype = ""
        self.width = 0
        self.height = 0
        self.fatal_error: str | None = None
        self.release_failed = False

    @property
    def worker_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is None:
            if self._worker_error is not None:
                raise RuntimeError(
                    "camera worker is in a failed shutdown state"
                ) from self._worker_error
            self.backend_name = "pending"
            self.backend_detail = ""
            self.failure_reason = None
            self.fatal_error = None
            self.release_failed = False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=_STOP_TIMEOUT)
            if self._thread.is_alive():
                raise TimeoutError("camera worker did not finish releasing resources")
            self._thread = None
        if self._worker_error is not None:
            raise RuntimeError(
                "camera worker failed while releasing resources"
            ) from self._worker_error

    def latest(self, after_seq: int, timeout: float) -> tuple[int, Image | None]:
        """Wait up to ``timeout`` for a frame newer than ``after_seq``."""

        with self._lock:
            if self._seq <= after_seq and not self._stop.is_set():
                self._lock.wait(timeout)
            if self._seq <= after_seq:
                return after_seq, None
            return self._seq, self._frame

    def _open(self) -> cv2.VideoCapture | None:
        return self._open_dshow(self.index)

    def _open_dshow(self, index: int) -> cv2.VideoCapture | None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            try:
                cap.release()
            except Exception as exc:
                self._record_release_failure(exc)
                raise
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._size[1])
        cap.set(cv2.CAP_PROP_FPS, 30)
        # Must come after the size: with DirectShow, requesting MJPG first is
        # silently undone by the resize and many webcams fall back to
        # uncompressed YUY2, which USB 2 caps at ~10 fps for 720p.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        backoff = ReconnectBackoff()
        try:
            while not self._stop.is_set():
                if self._use_shared and (self._run_shared(backoff) or self._stop.is_set()):
                    return
                if not self._exclusive_allowed():
                    self.backend_name = "yielded"
                    self.backend_detail = ""
                    self._set_disconnected()
                    if self._retry_wait(5.0):
                        return
                    continue
                if self._run_exclusive(backoff):
                    return
        except Exception as exc:
            self.failure_reason = type(exc).__name__
            self._worker_error = exc
            self.fatal_error = type(exc).__name__
            self._set_disconnected()

    def _run_shared(self, backoff: ReconnectBackoff) -> bool:
        """Return true only when shutdown (including release) completed cleanly."""

        capture: SharedCapture | None = None
        try:
            self._reset_connection_metadata()
            self.backend_name = "pending"
            self.failure_reason = None
            capture = self._shared_factory(self.index, *self._size)
            if self._stop.is_set():
                return True
            capture.open()
            self.backend_name = "shared"
            self.backend_detail = "winrt"
            last_ok = self._clock()
            window_start, window_frames = last_ok, 0
            while not self._stop.is_set():
                ok, frame = capture.read()
                now = self._clock()
                if not ok or frame is None or frame.size == 0:
                    if now - last_ok >= self._shared_timeout:
                        raise TimeoutError("shared camera produced no frames")
                    self._stop.wait(0.01)
                    continue
                last_ok = now
                self.last_frame_at = now
                self._update_metadata(capture, frame)
                backoff.recovered()
                window_frames += 1
                if now - window_start >= 1.0:
                    self.fps = window_frames / (now - window_start)
                    window_start, window_frames = now, 0
                self._publish(frame)
        except Exception as exc:
            self.failure_reason = type(exc).__name__
            if getattr(exc, "camera_fatal", False):
                self._worker_error = exc
                self.fatal_error = type(exc).__name__
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception as exc:
                    self._record_release_failure(exc)
                    self._set_disconnected()
                    return True
            self._set_disconnected()

        # Initialization, read, or no-frame failure falls back once to DSHOW.
        return self._stop.is_set() or self._worker_error is not None

    def _run_exclusive(self, backoff: ReconnectBackoff) -> bool:
        self.backend_name = "exclusive"
        while not self._stop.is_set():
            candidates: list[tuple[str, Callable[[], SharedCapture | cv2.VideoCapture | None]]]
            if self._injected_capture_factory:
                candidates = [("dshow", self._capture_factory)]
            else:
                candidates = [
                    (
                        "winrt",
                        lambda: _exclusive_capture(
                            self.index,
                            *self._size,
                            camera_name=self.camera_name,
                            camera_id=self.camera_id,
                            sharing_mode="exclusive",
                        ),
                    ),
                ]
                if not self.camera_id and not self.camera_name:
                    candidates.append(("dshow", self._open))
                else:
                    try:
                        use_dshow = self._sole_camera_matcher(
                            self.camera_id,
                            self.camera_name,
                            self._shared_timeout,
                        )
                    except Exception:
                        use_dshow = False
                    if use_dshow:
                        candidates.append(("dshow", lambda: self._open_dshow(0)))
            for detail, factory in candidates:
                if self._stop.is_set():
                    return True
                if not self._exclusive_allowed():
                    self.backend_name = "yielded"
                    self.backend_detail = ""
                    self._set_disconnected()
                    return bool(self._retry_wait(5.0))
                cap = None
                published = False
                try:
                    self._reset_connection_metadata()
                    cap = factory()
                    if cap is None:
                        continue
                    if self._stop.is_set():
                        return True
                    if detail == "winrt":
                        cast(SharedCapture, cap).open()
                    self.backend_name = "exclusive"
                    self.backend_detail = detail
                    last_ok = self._clock()
                    window_start, window_frames = last_ok, 0
                    while not self._stop.is_set():
                        ok, frame = cap.read()
                        now = self._clock()
                        if not ok or frame is None or frame.size == 0:
                            if now - last_ok >= _STALL_SECONDS:
                                break
                            self._stop.wait(0.01)
                            continue
                        last_ok = now
                        self.last_frame_at = now
                        normalized = np.asarray(frame, dtype=np.uint8)
                        self._update_metadata(cap, normalized)
                        backoff.recovered()
                        window_frames += 1
                        if now - window_start >= 1.0:
                            self.fps = window_frames / (now - window_start)
                            window_start, window_frames = now, 0
                        self._publish(normalized)
                        published = True
                except Exception as exc:
                    self.failure_reason = type(exc).__name__
                    if getattr(exc, "camera_fatal", False) or self.release_failed:
                        raise
                finally:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception as exc:
                            self._record_release_failure(exc)
                            raise
                self._set_disconnected()
                if self._stop.is_set():
                    return True
                if self._use_shared and detail == "winrt" and published:
                    return False
            if self._stop.is_set() or self._retry_wait(backoff.failed()):
                return True
            if self._use_shared:
                return False
        return True

    def _publish(self, frame: Image) -> None:
        with self._lock:
            self._frame = np.ascontiguousarray(frame)
            self._seq += 1
            self.connected = True
            self._lock.notify_all()

    def _update_metadata(self, capture: object, frame: Image) -> None:
        subtype = getattr(capture, "subtype", None)
        if subtype is None:
            subtype = self._opencv_fourcc(capture)
        self.subtype = str(subtype or "")
        width = getattr(capture, "width", None)
        height = getattr(capture, "height", None)
        self.width = int(width) if isinstance(width, (int, float)) and width > 0 else frame.shape[1]
        self.height = (
            int(height) if isinstance(height, (int, float)) and height > 0 else frame.shape[0]
        )
        capture_fps = getattr(capture, "fps", None)
        if capture_fps is None:
            capture_fps = self._opencv_property(capture, cv2.CAP_PROP_FPS)
        nominal_fps = float(capture_fps or 0.0)
        self.nominal_fps = nominal_fps if math.isfinite(nominal_fps) and nominal_fps > 0 else 0.0

    def _reset_connection_metadata(self) -> None:
        self.subtype = ""
        self.width = 0
        self.height = 0
        self.nominal_fps = 0.0

    @staticmethod
    def _opencv_property(capture: object, prop: int) -> float:
        getter = getattr(capture, "get", None)
        if not callable(getter):
            return 0.0
        try:
            return float(getter(prop) or 0.0)
        except (TypeError, ValueError, cv2.error):
            return 0.0

    @classmethod
    def _opencv_fourcc(cls, capture: object) -> str:
        raw_value = cls._opencv_property(capture, cv2.CAP_PROP_FOURCC)
        if not math.isfinite(raw_value) or raw_value <= 0 or raw_value > 0xFFFFFFFF:
            return ""
        value = int(raw_value)
        raw = bytes((value >> (8 * offset)) & 0xFF for offset in range(4))
        if any(byte < 32 or byte > 126 for byte in raw):
            return ""
        return raw.decode("ascii")

    def _set_disconnected(self) -> None:
        with self._lock:
            self.connected = False
            self._frame = None
            self.fps = 0.0
            self._lock.notify_all()

    def _record_release_failure(self, exc: Exception) -> None:
        error_name = type(exc).__name__
        self.release_failed = True
        self.failure_reason = error_name
        self._worker_error = exc
        self.fatal_error = error_name


def _shared_capture(
    index: int,
    width: int,
    height: int,
    *,
    camera_name: str = "",
    camera_id: str = "",
) -> SharedCapture:
    from desk_sentry.camera_shared import SharedCameraCapture

    return SharedCameraCapture(
        index,
        width,
        height,
        camera_name=camera_name,
        camera_id=camera_id,
    )


def _exclusive_capture(
    index: int,
    width: int,
    height: int,
    *,
    camera_name: str = "",
    camera_id: str = "",
    sharing_mode: Literal["exclusive"] = "exclusive",
) -> SharedCapture:
    from desk_sentry.camera_shared import SharedCameraCapture

    return SharedCameraCapture(
        index,
        width,
        height,
        camera_name=camera_name,
        camera_id=camera_id,
        sharing_mode=sharing_mode,
    )


def _sole_camera_matches(camera_id: str, camera_name: str, timeout: float) -> bool:
    from desk_sentry.camera_shared import sole_camera_matches

    return sole_camera_matches(camera_id, camera_name, timeout)
