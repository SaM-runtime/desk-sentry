"""Task 15 engine camera yielding, fatal recovery, and format diagnostics."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from desk_sentry.config import Settings
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, Snapshot, _LoopState
from desk_sentry.health import HealthState
from desk_sentry.sentry import Status


class Detector:
    def detect(self, _frame, score_threshold=0.4):
        return []


class CameraStub:
    def __init__(
        self,
        index: int = 0,
        *,
        frame: bool = False,
        backend_name: str = "shared",
        fatal_error: str | None = None,
    ) -> None:
        self.index = index
        self.connected = frame
        self.fps = 29.94
        self.nominal_fps = 30.0
        self.backend_name = backend_name
        self.backend_detail = "winrt"
        self.subtype = "NV12"
        self.width = 1920
        self.height = 1080
        self.failure_reason = fatal_error
        self.fatal_error = fatal_error
        self.worker_alive = fatal_error is None
        self.started = False
        self.stopped = False
        self._seq = 0
        self._frame = frame

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def latest(self, after_seq: int, timeout: float):
        if self.fatal_error is not None:
            pytest.fail("fatal worker must be discarded before latest()")
        if not self._frame:
            return after_seq, None
        self._seq += 1
        return self._seq, np.zeros((4, 6, 3), dtype=np.uint8)


def _settings(**changes) -> Settings:
    return replace(Settings(exclude_self=False), **changes)


def _records(folder: Path) -> list[dict]:
    result = []
    for path in folder.glob("*.jsonl.gz"):
        result.extend(json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines())
    return result


def test_real_camera_receives_name_and_thread_safe_exclusive_gate(monkeypatch) -> None:
    created = []

    def camera(index, **kwargs):
        created.append((index, kwargs))
        return CameraStub(index)

    monkeypatch.setattr("desk_sentry.engine.Camera", camera)
    engine = Engine(Detector(), _settings(camera_name="USB Camera"), lambda _snap: None)
    engine._step(_LoopState())
    index, kwargs = created[0]
    assert index == 0
    assert kwargs["camera_name"] == "USB Camera"
    assert kwargs["exclusive_allowed"]() is True
    engine.set_exclusive_allowed(False)
    assert kwargs["exclusive_allowed"]() is False
    engine.set_exclusive_allowed(True)
    assert kwargs["exclusive_allowed"]() is True
    engine.stop()


def test_camera_name_change_reopens_but_injected_factory_keeps_index_contract() -> None:
    created = []

    def factory(index: int):
        camera = CameraStub(index)
        created.append(camera)
        return camera

    initial = _settings(camera_name="Front")
    engine = Engine(Detector(), initial, lambda _snap: None, camera_factory=factory)
    engine._step(_LoopState())
    first = created[0]
    engine.apply(replace(initial, camera_name="Dock"))
    engine._sync_settings()
    assert first.stopped and engine._camera is None


@pytest.mark.parametrize("backend", ["exclusive", "yielded"])
def test_meeting_pause_snapshots_preserve_last_camera_backend(backend: str, monkeypatch) -> None:
    snapshots = []
    engine = Engine(Detector(), _settings(), snapshots.append, camera_factory=CameraStub)
    camera = CameraStub(backend_name=backend)
    engine._camera = camera
    monkeypatch.setattr(engine._wake, "wait", lambda _timeout: True)
    engine.apply(_settings(armed=False), pause_reason="meeting")
    state = _LoopState()
    engine._step(state)
    engine._step(state)
    assert camera.stopped
    assert [snapshot.status for snapshot in snapshots] == [Status.PAUSED, Status.PAUSED]
    assert [snapshot.camera_backend for snapshot in snapshots] == [backend, backend]
    assert all(snapshot.pause_reason == "meeting" for snapshot in snapshots)


def test_fatal_camera_worker_is_discarded_then_rebuilt_after_bounded_backoff(
    tmp_path: Path,
) -> None:
    snapshots = []
    dead = CameraStub(fatal_error="SharedCameraOperationStuckError")
    recovered = CameraStub(frame=True)
    cameras = iter((dead, recovered))
    engine = Engine(
        Detector(),
        _settings(),
        snapshots.append,
        camera_factory=lambda _index: next(cameras),
    )
    engine.diagnostics = DiagnosticsLog(tmp_path)
    state = _LoopState()

    engine._step(state)
    failed = snapshots[-1]
    assert engine._camera is None
    assert not dead.stopped  # the already-ended, sticky-failure object is discarded
    assert failed.health_state is HealthState.DEGRADED
    assert failed.health_reason == "camera_unresponsive"
    assert failed.last_camera_error == "SharedCameraOperationStuckError"
    assert 29 <= engine._camera_retry_at - time.monotonic() <= 30
    assert engine._camera_retry_delay == 60

    engine._camera_retry_at = time.monotonic() - 1
    engine._step(state)
    restored = snapshots[-1]
    assert engine._camera is recovered
    assert restored.health_state is HealthState.OK
    assert restored.health_reason is None
    assert restored.last_camera_error == "SharedCameraOperationStuckError"
    assert engine._camera_retry_delay == 30
    engine.diagnostics.flush()
    error = next(record for record in _records(tmp_path) if record["kind"] == "error")
    assert error == {
        "ts": error["ts"],
        "kind": "error",
        "error": "SharedCameraOperationStuckError",
    }


def test_repeated_fatal_workers_double_retry_to_five_minute_cap() -> None:
    cameras = iter(CameraStub(fatal_error="StuckError") for _ in range(6))
    engine = Engine(
        Detector(),
        _settings(),
        lambda _snapshot: None,
        camera_factory=lambda _index: next(cameras),
    )
    state = _LoopState()
    for scheduled, next_delay in (
        (30, 60),
        (60, 120),
        (120, 240),
        (240, 300),
        (300, 300),
        (300, 300),
    ):
        before = time.monotonic()
        engine._step(state)
        assert scheduled - 0.1 <= engine._camera_retry_at - before <= scheduled + 0.1
        assert engine._camera_retry_delay == next_delay
        engine._camera_retry_at = 0.0


def test_stop_discards_an_already_ended_fatal_camera_without_sticky_stop() -> None:
    engine = Engine(Detector(), _settings(), lambda _snapshot: None)
    dead = CameraStub(fatal_error="SharedCameraOperationStuckError")
    engine._camera = dead
    engine.stop()
    assert engine._camera is None
    assert not dead.stopped
    assert engine._last_camera_error == "SharedCameraOperationStuckError"


def test_camera_backend_and_changed_formats_are_diagnosed_without_device_identity(
    tmp_path: Path,
) -> None:
    snapshots = []
    engine = Engine(Detector(), _settings(camera_name="Private Camera"), snapshots.append)
    camera = CameraStub(frame=True)
    engine._camera = camera
    engine.diagnostics = DiagnosticsLog(tmp_path)

    engine._emit_snapshot(Snapshot(Status.CLEAR))
    camera.fps = 24.04
    engine._emit_snapshot(Snapshot(Status.CLEAR))
    camera.width = 1280
    engine._emit_snapshot(Snapshot(Status.CLEAR))
    camera.width = 1920
    camera.fps = 60.0
    engine._emit_snapshot(Snapshot(Status.CLEAR))
    engine.diagnostics.flush()

    records = _records(tmp_path)
    backends = [record for record in records if record["kind"] == "camera_backend"]
    assert len(backends) == 1  # fps changes are payload, not a new backend transition
    backend = backends[0]
    assert backend == {
        "ts": backend["ts"],
        "kind": "camera_backend",
        "backend": "shared",
        "backend_detail": "winrt",
        "subtype": "NV12",
        "width": 1920,
        "height": 1080,
        "nominal_fps": 30.0,
        "failure_reason": None,
    }
    formats = [record for record in records if record["kind"] == "camera_format"]
    assert [(record["width"], record["height"]) for record in formats] == [
        (1920, 1080),
        (1280, 1080),
    ]
    assert all("name" not in record and "id" not in record for record in records)
    latest = snapshots[-1]
    assert latest.backend_detail == "winrt"
    assert latest.subtype == "NV12"
    assert (latest.width, latest.height, latest.camera_fps) == (1920, 1080, 60.0)
