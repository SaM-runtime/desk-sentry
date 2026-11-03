"""Background webcam reader that always exposes only the newest frame."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np
from numpy.typing import NDArray

from desk_sentry.health import ReconnectBackoff

Image = NDArray[np.uint8]

_STALL_SECONDS = 3.0


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
    ) -> None:
        self.index = index
        self._size = (width, height)
        self._lock = threading.Condition()
        self._frame: Image | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._capture_factory = capture_factory or self._open
        self._retry_wait = retry_wait or self._stop.wait
        self._clock = clock
        self.last_frame_at: float | None = None
        self._thread: threading.Thread | None = None
        self.connected = False
        self.fps = 0.0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

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
        while not self._stop.is_set():
            cap = self._capture_factory()
            if cap is None:
                self._set_disconnected()
                if self._retry_wait(backoff.failed()):
                    break
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
                    with self._lock:
                        self._frame = np.ascontiguousarray(frame)
                        self._seq += 1
                        self.connected = True
                        self._lock.notify_all()
            finally:
                cap.release()
            self._set_disconnected()
            if not self._stop.is_set() and self._retry_wait(backoff.failed()):
                break

    def _set_disconnected(self) -> None:
        with self._lock:
            self.connected = False
            self._frame = None
            self.fps = 0.0
            self._lock.notify_all()
