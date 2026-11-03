"""Face features for telling labelled people apart (YuNet detector + SFace).

Runs only on the head region of a person box, never on the whole frame, and
only for the few tracks that need an identity, so it adds ~10-30 ms per face
and nothing while the scene is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from desk_sentry.detector import Box
from desk_sentry.people import Vector, normalize

Image = NDArray[np.uint8]

DETECTOR_MODEL = "face_detection_yunet_2026may.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
MIN_FACE_PIXELS = 28  # smaller faces give unreliable features
MIN_FACE_SCORE = 0.8
_HEAD_FRACTION = 0.6  # top part of a person box that can contain the face
_MAX_CROP_SIDE = 480


@dataclass(frozen=True, slots=True)
class Face:
    embedding: Vector
    box: Box  # in frame pixels
    score: float


class FaceEmbedder:
    def __init__(self, models: Path) -> None:
        self._detector = cv2.FaceDetectorYN.create(
            str(models / DETECTOR_MODEL), "", (320, 320), MIN_FACE_SCORE, 0.3, 20
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(models / RECOGNIZER_MODEL), "")

    @staticmethod
    def available(models: Path) -> bool:
        return (models / DETECTOR_MODEL).is_file() and (models / RECOGNIZER_MODEL).is_file()

    def embed(self, frame: Image, person: Box) -> Face | None:
        """Best face inside the head region of ``person``, or None."""

        height, width = frame.shape[:2]
        pad = person.width * 0.15
        left = max(0, int(person.x1 - pad))
        right = min(width, int(person.x2 + pad) + 1)
        top = max(0, int(person.y1 - person.height * 0.05))
        bottom = min(height, int(person.y1 + person.height * _HEAD_FRACTION) + 1)
        if right - left < MIN_FACE_PIXELS or bottom - top < MIN_FACE_PIXELS:
            return None
        crop: Image = frame[top:bottom, left:right]
        scale = min(1.0, _MAX_CROP_SIDE / max(crop.shape[:2]))
        if scale < 1.0:
            crop = np.asarray(
                cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), np.uint8
            )
        self._detector.setInputSize((crop.shape[1], crop.shape[0]))
        _, raw = self._detector.detect(crop)
        if raw is None or len(raw) == 0:
            return None
        faces = np.asarray(raw, dtype=np.float32)
        best = faces[int(np.argmax(faces[:, 2] * faces[:, 3]))]  # largest face
        if best[2] / scale < MIN_FACE_PIXELS or best[14] < MIN_FACE_SCORE:
            return None
        aligned = self._recognizer.alignCrop(crop, best)
        vector = normalize(self._recognizer.feature(aligned))
        if vector is None:
            return None
        x, y, w, h = (float(v) / scale for v in best[:4])
        return Face(vector, Box(left + x, top + y, left + x + w, top + y + h), float(best[14]))
