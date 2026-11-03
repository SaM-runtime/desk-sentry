"""Shared fakes for engine-level tests (no camera, no model)."""

import threading
import time
from collections.abc import Callable

import numpy as np

from desk_sentry.config import Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.engine import Engine, Snapshot

Frame = np.ndarray


class FakeCamera:
    """Serves synthetic frames at ~60 fps; ``connected=False`` simulates unplugging."""

    instances: list["FakeCamera"] = []

    def __init__(self, index: int, connected: bool = True) -> None:
        self.index = index
        self.connected = connected
        self.fps = 60.0
        self.started = self.stopped = False
        self._seq = 0
        FakeCamera.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def latest(self, after_seq: int, timeout: float) -> tuple[int, Frame | None]:
        time.sleep(1 / 60)
        if not self.connected:
            return after_seq, None
        self._seq += 1
        frame = np.full((360, 640, 3), self._seq % 250, dtype=np.uint8)  # always "moving"
        return self._seq, frame


class ScriptedDetector:
    """Returns a person walking across the frame after ``enter_after`` seconds."""

    def __init__(self, enter_after: float = 0.0, fail_first: int = 0) -> None:
        self.start = time.monotonic()
        self.enter_after = enter_after
        self.fail_first = fail_first
        self.calls = 0

    def detect(self, frame: Frame, score_threshold: float = 0.4) -> list[Detection]:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise RuntimeError("inference blew up")
        t = time.monotonic() - self.start
        if t < self.enter_after:
            return []
        x = 20 + 400 * (t - self.enter_after)
        return [Detection(Box(x, 60, x + 60, 300), 0.9)]


class Recorder:
    def __init__(self) -> None:
        self.items: list[Snapshot] = []
        self.lock = threading.Lock()

    def __call__(self, snap: Snapshot) -> None:
        with self.lock:
            self.items.append(snap)

    def wait_for(self, predicate: Callable[[Snapshot], bool], timeout: float = 5.0) -> Snapshot:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for snap in self.items:
                    if predicate(snap):
                        return snap
            time.sleep(0.01)
        raise AssertionError("condition not reached")


def make(detector: ScriptedDetector, settings: Settings | None = None, **camera_kwargs: bool):
    FakeCamera.instances.clear()
    rec = Recorder()
    engine = Engine(
        detector,
        settings or Settings(exclude_self=False),
        rec,
        camera_factory=lambda index: FakeCamera(index, **camera_kwargs),
    )
    return engine, rec


def make_engine(enter_after: float = 0.0, fail_first: int = 0, **camera_kwargs: bool):
    detector = ScriptedDetector(enter_after=enter_after, fail_first=fail_first)
    engine, rec = make(detector, **camera_kwargs)
    return engine, rec, detector
