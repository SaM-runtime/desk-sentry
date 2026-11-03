"""Cheap low-resolution frame differencing: scheduling and entry evidence."""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np
from numpy.typing import NDArray

from desk_sentry.detector import Box

Image = NDArray[np.uint8]

_SIZE = (160, 90)
_PIXEL_DELTA = 20
_GATE_FRACTION = 0.004
_REFERENCE_AGE = 0.25  # compare against the scene ~a quarter second ago
_HISTORY_SECONDS = 1.0


class MotionMap:
    def __init__(self) -> None:
        self._history: deque[tuple[float, Image]] = deque()
        self._mask: NDArray[np.bool_] | None = None
        self._frame_size = (1, 1)

    def push(self, frame: Image, now: float) -> bool:
        """Add a camera frame. Returns True if anything changed since the last frame."""

        gray = np.asarray(
            cv2.GaussianBlur(
                cv2.resize(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), _SIZE, interpolation=cv2.INTER_AREA
                ),
                (3, 3),
                0,
            ),
            dtype=np.uint8,
        )
        height, width = frame.shape[:2]
        self._frame_size = (width, height)
        previous = self._history[-1][1] if self._history else None
        self._history.append((now, gray))
        while self._history and now - self._history[0][0] > _HISTORY_SECONDS:
            self._history.popleft()

        reference = next(
            (g for t, g in reversed(self._history) if now - t >= _REFERENCE_AGE),
            self._history[0][1],
        )
        self._mask = cv2.absdiff(reference, gray) >= _PIXEL_DELTA
        if previous is None:
            return True
        changed = np.count_nonzero(cv2.absdiff(previous, gray) >= _PIXEL_DELTA)
        return bool(changed / gray.size >= _GATE_FRACTION)

    def fraction(self, box: Box) -> float:
        """Share of pixels inside ``box`` (frame pixels) that changed recently."""

        if self._mask is None:
            return 0.0
        width, height = self._frame_size
        mw, mh = _SIZE
        x1 = int(np.clip(box.x1 / width * mw, 0, mw - 1))
        x2 = int(np.clip(np.ceil(box.x2 / width * mw), x1 + 1, mw))
        y1 = int(np.clip(box.y1 / height * mh, 0, mh - 1))
        y2 = int(np.clip(np.ceil(box.y2 / height * mh), y1 + 1, mh))
        region = self._mask[y1:y2, x1:x2]
        return float(region.mean()) if region.size else 0.0
