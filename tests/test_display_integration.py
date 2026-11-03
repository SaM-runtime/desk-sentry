from pathlib import Path

import numpy as np
import pytest

from desk_sentry.camera import Image
from desk_sentry.config import Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.sentry import Role, Status
from test_diagnostics import read
from tests_support import FakeCamera


class ConvergingDetector:
    """Two separate people whose boxes overlap on the second analysis frame."""

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame: Image, score_threshold: float = 0.4) -> list[Detection]:
        self.calls += 1
        boxes = (
            [Box(0, 0, 100, 300), Box(200, 0, 300, 300)]
            if self.calls == 1
            else [Box(0, 0, 180, 300), Box(80, 0, 300, 300)]
        )
        return [Detection(box, 0.9) for box in boxes]


def test_snapshot_filters_cards_without_filtering_assessment_or_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 10.0
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now)
    snapshots: list[Snapshot] = []
    engine = Engine(
        ConvergingDetector(),
        Settings(exclude_self=False),
        snapshots.append,
        camera_factory=FakeCamera,
    )
    engine.diagnostics = DiagnosticsLog(tmp_path, clock=lambda: now)
    state = _LoopState()
    try:
        engine._step(state)
        now = 10.5
        engine._step(state)
        snap = snapshots[-1]
        assert len(engine._tracker.tracks) == 2
        assert len(snap.tracks) == 1
        assert snap.people == state.assessment.people == 2
        assert snap.intruders == state.assessment.intruders == 0
        assert snap.status is state.assessment.status is Status.WATCHING
        assert snap.tracks[0].role is Role.STILL
        assert snap.frame is not None and snap.frame.dtype == np.uint8
        # Exercise the same snapshot assembly with already-assessed alert fields.
        state.assessment.roles = {1: Role.INTRUDER, 2: Role.PENDING}
        state.assessment.status = Status.ALERT
        state.assessment.intruders = 1
        state.assessment.alert_started = True
        now = 10.53  # preview-only step; no new detection/assessment
        engine._step(state)
        alert = snapshots[-1]
        assert [t.track_id for t in alert.tracks] == [1]
        assert alert.status is Status.ALERT and alert.alert_started
        assert alert.intruders == 1 and alert.people == 2
    finally:
        engine.stop()

    analysis = [r for r in read(tmp_path) if r["kind"] == "analysis"]
    assert len(analysis[-1]["tracks"]) == 2
    assert {t["id"] for t in analysis[-1]["tracks"]} == {1, 2}
    assert all(t["seen"] for t in analysis[-1]["tracks"])
