"""COCO person detection with the OpenCV Zoo NanoDet-Plus model (Apache-2.0).

Why not the v1 MP-PersonDet: that model is MediaPipe's pose *detector*, which
uses the face as its anchor. It misses people seen from behind, from the side
or far away — exactly the people a rear-view warning has to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]

INPUT_SIZE = 416
INFERENCE_THREADS = 2
_STRIDES = (8, 16, 32)
_REG_MAX = 7
_NUM_CLASSES = 80
_PERSON_CLASS = 0
_MEAN = np.array([103.53, 116.28, 123.675], dtype=np.float32)
_STD = np.array([57.375, 57.12, 58.395], dtype=np.float32)


@dataclass(frozen=True, slots=True)
class Box:
    """Axis-aligned box in source-frame pixels."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def iou(self, other: Box) -> float:
        iw = min(self.x2, other.x2) - max(self.x1, other.x1)
        ih = min(self.y2, other.y2) - max(self.y1, other.y1)
        if iw <= 0.0 or ih <= 0.0:
            return 0.0
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0.0 else 0.0

    def scaled(self, sx: float, sy: float) -> Box:
        return Box(self.x1 * sx, self.y1 * sy, self.x2 * sx, self.y2 * sy)


@dataclass(frozen=True, slots=True)
class Detection:
    box: Box
    score: float


def _anchor_centers() -> list[NDArray[np.float32]]:
    centers = []
    for stride in _STRIDES:
        size = INPUT_SIZE // stride
        xs, ys = np.meshgrid(np.arange(size), np.arange(size))
        cx = xs.reshape(-1) * stride + 0.5 * (stride - 1)
        cy = ys.reshape(-1) * stride + 0.5 * (stride - 1)
        centers.append(np.column_stack((cx, cy)).astype(np.float32))
    return centers


def letterbox(frame: Image) -> tuple[NDArray[np.float32], float]:
    """Resize keeping aspect ratio, pad bottom/right, normalize to an NCHW blob."""

    h, w = frame.shape[:2]
    scale = min(INPUT_SIZE / h, INPUT_SIZE / w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    canvas = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.float32)
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32)
    canvas[:nh, :nw] = (resized - _MEAN) / _STD
    # Padding stays 0, i.e. the dataset mean after normalization.
    return canvas.transpose(2, 0, 1)[np.newaxis].copy(), scale


def decode(
    outputs: list[NDArray[np.float32]],
    *,
    scale: float,
    frame_size: tuple[int, int],
    score_threshold: float,
    nms_threshold: float,
    anchors: list[NDArray[np.float32]] | None = None,
) -> list[Detection]:
    """Turn raw NanoDet head outputs into person detections in frame pixels.

    Outputs are matched by shape, not by order: OpenCV 5's graph engine does
    not keep the ONNX output order that the upstream demo relies on.
    """

    anchors = anchors if anchors is not None else _anchor_centers()
    squeezed = [np.asarray(o, dtype=np.float32).reshape(-1, o.shape[-1]) for o in outputs]
    cls_by_rows = {o.shape[0]: o for o in squeezed if o.shape[1] == _NUM_CLASSES}
    reg_by_rows = {o.shape[0]: o for o in squeezed if o.shape[1] == 4 * (_REG_MAX + 1)}

    project = np.arange(_REG_MAX + 1, dtype=np.float32)
    boxes: list[NDArray[np.float32]] = []
    scores: list[NDArray[np.float32]] = []
    for stride, centers in zip(_STRIDES, anchors, strict=True):
        rows = centers.shape[0]
        if rows not in cls_by_rows or rows not in reg_by_rows:
            raise ValueError(f"missing NanoDet output for stride {stride}")
        person = cls_by_rows[rows][:, _PERSON_CLASS]
        keep = person >= score_threshold
        if not np.any(keep):
            continue
        reg = reg_by_rows[rows][keep].reshape(-1, _REG_MAX + 1)
        reg = np.exp(reg - reg.max(axis=1, keepdims=True))
        dist = (reg / reg.sum(axis=1, keepdims=True)) @ project
        dist = dist.reshape(-1, 4) * stride
        c = centers[keep]
        boxes.append(np.column_stack((c - dist[:, :2], c + dist[:, 2:])))
        scores.append(person[keep])

    if not boxes:
        return []
    xyxy = np.concatenate(boxes) / scale
    conf = np.concatenate(scores)
    w, h = frame_size
    xyxy[:, (0, 2)] = np.clip(xyxy[:, (0, 2)], 0, w)
    xyxy[:, (1, 3)] = np.clip(xyxy[:, (1, 3)], 0, h)
    xywh = np.column_stack((xyxy[:, :2], xyxy[:, 2:] - xyxy[:, :2]))
    idx = cv2.dnn.NMSBoxes(xywh.tolist(), conf.tolist(), score_threshold, nms_threshold)
    result = []
    for i in np.asarray(idx, dtype=np.int32).reshape(-1):
        x1, y1, x2, y2 = (float(v) for v in xyxy[i])
        if x2 > x1 and y2 > y1:
            result.append(Detection(Box(x1, y1, x2, y2), float(conf[i])))
    return result


class PersonDetector:
    """CPU NanoDet-Plus person detector (~15–25 ms per frame on a laptop CPU)."""

    def __init__(self, model_path: Path, *, nms_threshold: float = 0.5) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"model not found: {model_path}")
        # OpenCV defaults to one worker per core and those workers busy-wait
        # between jobs. Measured at 10 Hz on a 20-core laptop: 2 threads gives
        # the same ~20 ms latency with ~4x less total CPU (47 % vs 185 % of a core).
        cv2.setNumThreads(INFERENCE_THREADS)
        self._net = cv2.dnn.readNet(str(model_path))
        self._outputs = self._net.getUnconnectedOutLayersNames()
        self._anchors = _anchor_centers()
        self._nms = nms_threshold

    def detect(self, frame: Image, score_threshold: float = 0.4) -> list[Detection]:
        blob, scale = letterbox(frame)
        self._net.setInput(blob)
        outputs = [np.asarray(o, dtype=np.float32) for o in self._net.forward(self._outputs)]
        h, w = frame.shape[:2]
        return decode(
            outputs,
            scale=scale,
            frame_size=(w, h),
            score_threshold=score_threshold,
            nms_threshold=self._nms,
            anchors=self._anchors,
        )
