from pathlib import Path

import numpy as np
import pytest

from desk_sentry import config
from desk_sentry.config import Settings
from desk_sentry.detector import INPUT_SIZE, Box, PersonDetector, decode, letterbox

MODEL = Path(__file__).resolve().parents[1] / "models" / "object_detection_nanodet_2022nov.onnx"


def test_letterbox_shape_and_scale() -> None:
    blob, scale = letterbox(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert blob.shape == (1, 3, INPUT_SIZE, INPUT_SIZE)
    assert scale == pytest.approx(INPUT_SIZE / 1280)


def test_decode_is_independent_of_output_order() -> None:
    rows = [(INPUT_SIZE // s) ** 2 for s in (8, 16, 32)]
    cls = [np.zeros((1, r, 80), np.float32) for r in rows]
    reg = [np.zeros((1, r, 32), np.float32) for r in rows]
    cls[1][0, 10, 0] = 0.9  # one person on the stride-16 grid
    reg[1][0, 10, :] = np.tile(np.eye(8, dtype=np.float32)[4] * 20, 4)  # distance 4 bins
    a = decode(cls + reg, scale=1.0, frame_size=(416, 416), score_threshold=0.5, nms_threshold=0.5)
    b = decode(
        reg[::-1] + cls[::-1],
        scale=1.0,
        frame_size=(416, 416),
        score_threshold=0.5,
        nms_threshold=0.5,
    )
    assert len(a) == 1 and a == b
    box = a[0].box
    assert box.width == pytest.approx(4 * 16 * 2, abs=1)


def test_box_iou() -> None:
    assert Box(0, 0, 10, 10).iou(Box(0, 0, 10, 10)) == pytest.approx(1.0)
    assert Box(0, 0, 10, 10).iou(Box(20, 20, 30, 30)) == 0.0


@pytest.mark.skipif(not MODEL.is_file(), reason="model not downloaded")
def test_model_runs_on_blank_frame() -> None:
    detector = PersonDetector(MODEL)
    assert detector.detect(np.zeros((480, 640, 3), np.uint8)) == []


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    original = Settings(camera_index=1, sensitivity="high", self_zone=(0.1, 0.2, 0.8, 1.0))
    config.save(original, path)
    assert config.load(path) == original


def test_settings_sanitizes_bad_values(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text(
        '{"sensitivity": "extreme", "action": "format-c", "self_zone": [0.9, 0, 0.1, 1],'
        ' "camera_index": 99, "unknown": 1}',
        encoding="utf-8",
    )
    loaded = config.load(path)
    assert loaded.sensitivity == "medium"
    assert loaded.action == "none"
    assert loaded.self_zone is None
    assert loaded.camera_index == 9


def test_settings_corrupt_file_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert config.load(path) == Settings()
