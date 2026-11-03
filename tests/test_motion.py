import numpy as np

from desk_sentry.detector import Box
from desk_sentry.motion import MotionMap


def test_motion_fraction_is_local_to_the_box() -> None:
    mm = MotionMap()
    frame = np.zeros((720, 1280, 3), np.uint8)
    for i in range(5):
        mm.push(frame, i * 0.1)
    moved = frame.copy()
    moved[100:400, 900:1100] = 255  # something appears on the right
    assert mm.push(moved, 0.5)
    assert mm.fraction(Box(900, 100, 1100, 400)) > 0.8
    assert mm.fraction(Box(100, 100, 300, 400)) == 0.0


def test_static_scene_has_no_motion() -> None:
    mm = MotionMap()
    frame = np.full((480, 640, 3), 90, np.uint8)
    results = [mm.push(frame, i * 0.1) for i in range(6)]
    assert results[0] is True  # first frame always schedules a detection
    assert not any(results[1:])
    assert mm.fraction(Box(0, 0, 640, 480)) == 0.0
