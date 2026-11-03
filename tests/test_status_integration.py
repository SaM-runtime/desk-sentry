from pathlib import Path

import pytest

from desk_sentry.camera import Image
from desk_sentry.config import Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.sentry import Role, Status
from test_diagnostics import read
from tests_support import FakeCamera


class FrameDetector:
    def __init__(self, frames: list[list[Detection]]) -> None:
        self.frames = iter(frames)

    def detect(self, frame: Image, score_threshold: float = 0.4) -> list[Detection]:
        return next(self.frames, [])


def test_pending_visibility_uses_latest_analysis_not_camera_only_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 10.0
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now)
    snapshots: list[Snapshot] = []
    engine = Engine(
        FrameDetector([[Detection(Box(100, 0, 200, 300), 0.9)], []]),
        Settings(exclude_self=False),
        snapshots.append,
        camera_factory=FakeCamera,
    )
    engine.diagnostics = DiagnosticsLog(tmp_path, clock=lambda: now * 10)
    state = _LoopState()
    try:
        engine._step(state)
        assert len(snapshots[-1].tracks) == 1
        assert snapshots[-1].tracks[0].role is Role.PENDING
        assert snapshots[-1].tracks[0].seen
        now = 10.04  # camera-only preview, no analysis
        engine._step(state)
        assert snapshots[-1].tracks == snapshots[-2].tracks
        now = 10.125  # missed analysis, still within the existing 0.35s view age limit
        engine._step(state)
        assert snapshots[-1].tracks == ()
        assert snapshots[-1].people == 1
        assert len(engine._tracker.tracks) == 1
    finally:
        engine.stop()
    records = [r for r in read(tmp_path) if r["kind"] == "analysis"]
    assert len(records[-1]["tracks"]) == 1
    assert records[-1]["tracks"][0]["role"] == "pending"
    assert records[-1]["tracks"][0]["seen"] is False


def test_watching_is_smoothed_only_in_snapshot_and_not_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 10.0
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now)
    snapshots: list[Snapshot] = []
    engine = Engine(
        FrameDetector([[Detection(Box(100, 0, 200, 300), 0.9)], [], [], []]),
        Settings(exclude_self=False),
        snapshots.append,
        camera_factory=FakeCamera,
    )
    engine.diagnostics = DiagnosticsLog(tmp_path, clock=lambda: now)
    state = _LoopState()
    try:
        engine._step(state)
        now = 11.0
        engine._step(state)
        assert snapshots[-1].status is Status.WATCHING
        now = 11.04  # re-displaying cached WATCHING must not restart its deadline
        engine._step(state)
        assert snapshots[-1].status is Status.WATCHING
        now = 11.625  # track expired, but WATCHING was last observed at 11.0
        engine._step(state)
        assert state.assessment.status is Status.CLEAR
        assert snapshots[-1].raw_status is Status.CLEAR
        assert snapshots[-1].status is Status.WATCHING
        assert snapshots[-1].people == snapshots[-1].intruders == 0
        now = 12.0  # exactly one second since last raw WATCHING
        engine._step(state)
        assert snapshots[-1].status is Status.CLEAR
    finally:
        engine.stop()
    records = {r["mono"]: r for r in read(tmp_path) if r["kind"] == "analysis"}
    assert records[11.625]["status"] == "clear"
    assert records[11.625]["tracks"] == []
    assert not records[11.625]["alert_started"]


def test_approaching_smoothing_keeps_raw_alert_events_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 10.0
    monkeypatch.setattr("desk_sentry.engine.time.monotonic", lambda: now)
    snapshots: list[Snapshot] = []
    growing = [Box(250 - i * 3, 0, 350 + i * 3, 200 + i * 10) for i in range(16)]
    frames = [[], *[[Detection(box, 0.9)] for box in growing + [growing[-1]] * 24]]
    engine = Engine(
        FrameDetector(frames),
        Settings(exclude_self=False, stationary_mute_seconds=0),
        snapshots.append,
        camera_factory=FakeCamera,
    )
    engine.diagnostics = DiagnosticsLog(tmp_path, clock=lambda: now)
    state = _LoopState()
    by_time: dict[float, Snapshot] = {}
    try:
        engine._step(state)  # warm up on an empty scene
        for i in range(40):
            now = 13.0 + i * 0.125
            engine._step(state)
            by_time[now] = snapshots[-1]
    finally:
        engine.stop()
    records = [r for r in read(tmp_path) if r["kind"] == "analysis" and r["mono"] in by_time]
    assert any(r["status"] == "approaching" for r in records)
    assert any(
        r["status"] == "alert" and by_time[r["mono"]].status is Status.APPROACHING for r in records
    )
    assert snapshots[-1].status is Status.ALERT
    assert sum(s.alert_started for s in snapshots) == 1
    for record in records:
        snapshot = by_time[record["mono"]]
        assert snapshot.alert_started == record["alert_started"]
        assert snapshot.continued == record["continued"]
        assert snapshot.people == 1
        assert snapshot.intruders == (0 if record["mono"] == 13.0 else 1)
        assert len(record["tracks"]) == 1 and record["tracks"][0]["seen"]
