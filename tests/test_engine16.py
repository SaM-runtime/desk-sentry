"""Task 16 engine retry identity and camera FPS diagnostics."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from desk_sentry.config import Settings
from desk_sentry.engine import CAMERA_FATAL_RETRY_INITIAL, Engine, Snapshot, _LoopState


class Detector:
    def detect(self, _frame, score_threshold=0.4):
        return []


class CameraStub:
    def __init__(self, index: int = 0, *, fatal: bool = False, frame: bool = False) -> None:
        self.index = index
        self.connected = frame
        self.fps = 17.25
        self.nominal_fps = 30.0
        self.backend_name = "shared"
        self.backend_detail = "winrt"
        self.subtype = "NV12"
        self.width = 1280
        self.height = 720
        self.failure_reason = "StuckError" if fatal else None
        self.fatal_error = "StuckError" if fatal else None
        self.worker_alive = not fatal
        self._frame = frame
        self._seq = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def latest(self, after_seq: int, timeout: float):
        if not self._frame:
            return after_seq, None
        self._seq += 1
        return self._seq, np.zeros((4, 6, 3), dtype=np.uint8)


class Records:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def write(self, kind: str, **fields: object) -> None:
        self.items.append({"kind": kind, **fields})

    def analysis(self, **fields: object) -> None:
        self.items.append({"kind": "analysis", **fields})


def _settings(**changes) -> Settings:
    return replace(Settings(exclude_self=False), **changes)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("camera_index", 1),
        ("camera_id", "second-id"),
        ("camera_name", "Dock Camera"),
        ("camera_mode", "exclusive"),
    ],
)
def test_changed_camera_identity_reopens_immediately_after_fatal(
    monkeypatch, field: str, value: object
) -> None:
    created: list[CameraStub] = []

    def factory(index: int) -> CameraStub:
        camera = CameraStub(index, fatal=not created)
        created.append(camera)
        return camera

    initial = _settings(camera_id="first-id", camera_name="Front Camera")
    engine = Engine(Detector(), initial, lambda _snapshot: None, camera_factory=factory)
    state = _LoopState()
    engine._step(state)
    assert len(created) == 1 and engine._camera_retry_delay > CAMERA_FATAL_RETRY_INITIAL

    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    engine._wake.clear()
    engine.apply(replace(initial, **{field: value}))
    assert engine._wake.is_set()
    engine._step(state)

    assert len(created) == 2
    assert engine._camera is created[1]
    assert engine._camera_retry_delay == CAMERA_FATAL_RETRY_INITIAL


def test_same_camera_identity_keeps_fatal_retry_across_pause_resume(monkeypatch) -> None:
    created: list[CameraStub] = []

    def factory(index: int) -> CameraStub:
        camera = CameraStub(index, fatal=True)
        created.append(camera)
        return camera

    initial = _settings(camera_id="same-id")
    engine = Engine(Detector(), initial, lambda _snapshot: None, camera_factory=factory)
    state = _LoopState()
    engine._step(state)
    retry_at = engine._camera_retry_at
    retry_delay = engine._camera_retry_delay

    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    engine._step(state)
    assert len(created) == 1
    assert engine._camera_retry_at == retry_at
    assert engine._camera_retry_delay == retry_delay

    engine.apply(replace(initial, armed=False), pause_reason="manual")
    engine._step(state)
    engine.apply(initial)
    engine._step(state)

    assert len(created) == 1
    assert engine._camera_retry_at == retry_at
    assert engine._camera_retry_delay == retry_delay


def test_camera_events_use_nominal_fps_but_analysis_uses_measured_fps() -> None:
    snapshots: list[Snapshot] = []
    engine = Engine(
        Detector(),
        _settings(camera_id="private-device-id", camera_name="Private Camera"),
        snapshots.append,
        camera_factory=lambda index: CameraStub(index, frame=True),
    )
    records = Records()
    engine.diagnostics = records  # type: ignore[assignment]

    engine._step(_LoopState())

    camera_events = [
        record for record in records.items if record["kind"] in ("camera_backend", "camera_format")
    ]
    assert camera_events
    assert all(record["nominal_fps"] == 30.0 and "fps" not in record for record in camera_events)
    analysis = next(record for record in records.items if record["kind"] == "analysis")
    assert analysis["camera_fps"] == 17.2
    assert snapshots[-1].camera_fps == 17.25
    assert snapshots[-1].nominal_fps == 30.0
    assert all("private" not in str(record).lower() for record in records.items)
