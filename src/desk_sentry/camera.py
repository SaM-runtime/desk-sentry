"""Background webcam reader that always exposes only the newest frame."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Literal, Protocol

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
    ) -> None:
        if mode not in ("auto", "exclusive"):
            raise ValueError("camera mode must be 'auto' or 'exclusive'")
        self.index = index
        self._size = (width, height)
        self._lock = threading.Condition()
        self._frame: Image | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._capture_factory = capture_factory or self._open
        self._shared_factory = shared_factory or _shared_capture
        # Existing injected OpenCV tests must not begin enumerating real devices.
        self._use_shared = mode == "auto" and (
            capture_factory is None or shared_factory is not None
        )
        self._shared_timeout = shared_timeout
        self._retry_wait = retry_wait or self._stop.wait
        self._clock = clock
        self.last_frame_at: float | None = None
        self._thread: threading.Thread | None = None
        self._worker_error: Exception | None = None
        self.connected = False
        self.fps = 0.0
        self.backend_name: Literal["shared", "exclusive", "pending"] = "pending"
        self.failure_reason: str | None = None

    def start(self) -> None:
        if self._thread is None:
            if self._worker_error is not None:
                raise RuntimeError(
                    "camera worker is in a failed shutdown state"
                ) from self._worker_error
            self.backend_name = "pending"
            self.failure_reason = None
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
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
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
                if self._run_exclusive(backoff):
                    return
        except Exception as exc:
            self.failure_reason = type(exc).__name__
            self._worker_error = exc
            self._set_disconnected()

    def _run_shared(self, backoff: ReconnectBackoff) -> bool:
        """Return true only when shutdown (including release) completed cleanly."""

        capture: SharedCapture | None = None
        try:
            self.backend_name = "pending"
            self.failure_reason = None
            capture = self._shared_factory(self.index, *self._size)
            capture.open()
            self.backend_name = "shared"
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
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception as exc:
                    self.failure_reason = type(exc).__name__
                    self._worker_error = exc
                    self._set_disconnected()
                    return True
            self._set_disconnected()

        # Initialization, read, or no-frame failure falls back once to DSHOW.
        return self._stop.is_set() or self._worker_error is not None

    def _run_exclusive(self, backoff: ReconnectBackoff) -> bool:
        self.backend_name = "exclusive"
        while not self._stop.is_set():
            cap = self._capture_factory()
            if cap is None:
                self._set_disconnected()
                if self._retry_wait(backoff.failed()):
                    return True
                if self._use_shared:
                    return False
                continue
            last_ok = self._clock()
            window_start, window_frames = last_ok, 0
            try:
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
                    backoff.recovered()
                    window_frames += 1
                    if now - window_start >= 1.0:
                        self.fps = window_frames / (now - window_start)
                        window_start, window_frames = now, 0
                    self._publish(np.asarray(frame, dtype=np.uint8))
            finally:
                cap.release()
            self._set_disconnected()
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

    def _set_disconnected(self) -> None:
        with self._lock:
            self.connected = False
            self._frame = None
            self.fps = 0.0
            self._lock.notify_all()


def _shared_capture(index: int, width: int, height: int) -> SharedCapture:
    from desk_sentry.camera_shared import SharedCameraCapture

    return SharedCameraCapture(index, width, height)
