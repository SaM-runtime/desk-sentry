import time

from desk_sentry.config import Settings
from desk_sentry.sentry import Status
from tests_support import FakeCamera, ScriptedDetector, make


def test_walker_entering_after_warmup_triggers_one_alert() -> None:
    engine, rec = make(ScriptedDetector(enter_after=2.5))
    engine.start()
    try:
        started = rec.wait_for(lambda s: s.alert_started, timeout=6)
        assert started.status in (Status.ALERT, Status.APPROACHING)
        assert started.frame is not None
        time.sleep(0.5)
    finally:
        engine.stop()
    assert sum(s.alert_started for s in rec.items) == 1


def test_detector_crash_reports_error_and_recovers() -> None:
    engine, rec = make(ScriptedDetector(enter_after=99, fail_first=1))
    engine.start()
    try:
        rec.wait_for(lambda s: s.status is Status.ERROR)
        rec.wait_for(lambda s: s.status is Status.CLEAR and s.frame is not None, timeout=6)
    finally:
        engine.stop()
    assert engine.error_count == 1
    assert FakeCamera.instances[0].stopped  # camera released while backing off


def test_pause_releases_camera_and_resume_reopens() -> None:
    engine, rec = make(ScriptedDetector(enter_after=99))
    engine.start()
    try:
        rec.wait_for(lambda s: s.status is Status.CLEAR)
        engine.apply(Settings(exclude_self=False, armed=False))
        rec.wait_for(lambda s: s.status is Status.PAUSED)
        assert FakeCamera.instances[0].stopped
        engine.apply(Settings(exclude_self=False, armed=True))
        deadline = time.monotonic() + 3
        while len(FakeCamera.instances) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        engine.stop()
    assert len(FakeCamera.instances) == 2


def test_switching_camera_index_reopens() -> None:
    engine, rec = make(ScriptedDetector(enter_after=99))
    engine.start()
    try:
        rec.wait_for(lambda s: s.status is Status.CLEAR)
        engine.apply(Settings(exclude_self=False, camera_index=1))
        deadline = time.monotonic() + 3
        while not any(c.index == 1 for c in FakeCamera.instances) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        engine.stop()
    assert FakeCamera.instances[0].stopped
    assert FakeCamera.instances[-1].index == 1


def test_disconnected_camera_reports_no_camera() -> None:
    engine, rec = make(ScriptedDetector(), connected=False)
    engine.start()
    try:
        rec.wait_for(lambda s: s.status is Status.NO_CAMERA)
    finally:
        engine.stop()


def test_hidden_preview_sends_status_only_snapshots() -> None:
    engine, rec = make(ScriptedDetector(enter_after=99))
    engine.preview_wanted = False
    engine.start()
    try:
        time.sleep(1.0)
    finally:
        engine.stop()
    live = [s for s in rec.items if s.status is Status.CLEAR]
    assert live and all(s.frame is None for s in live)
    assert len(live) <= 8  # ~5 Hz instead of per-frame


def test_alert_snapshot_carries_a_small_mirrored_thumbnail() -> None:
    engine, rec = make(ScriptedDetector(enter_after=2.5))
    engine.start()
    try:
        snap = rec.wait_for(lambda s: s.alert_thumbnail is not None, timeout=6)
    finally:
        engine.stop()
    thumb = snap.alert_thumbnail
    assert thumb is not None and thumb.dtype.name == "uint8" and thumb.shape[2] == 3
    assert thumb.shape[0] <= 160 and thumb.shape[1] <= 240
    assert sum(s.alert_thumbnail is not None for s in rec.items) == 1
