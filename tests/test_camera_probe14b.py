"""Task 14b camera-share probe verdicts without physical camera access."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


def load_probe(name: str):
    path = Path(__file__).parents[1] / "tools" / "camera_share_probe.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe


class TickClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.05
        return self.now


class SharedCamera:
    backend_name = "shared"
    failure_reason = None

    def __init__(self) -> None:
        self.seq = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def latest(self, after_seq: int, _timeout: float):
        self.seq = after_seq + 1
        return self.seq, np.zeros((1, 1, 3), np.uint8)


class ContinuousPeer:
    def __init__(self) -> None:
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV-compatible boundary
        return True

    def read(self):
        return True, np.zeros((1, 1, 3), np.uint8)

    def release(self) -> None:
        self.released = True


class FailedPeer:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV-compatible boundary
        raise self.error

    def read(self):
        raise AssertionError("failed peer must not be sampled")

    def release(self) -> None:
        self.released = True


def test_msmf_failure_is_report_only_and_dshow_can_prove_sharing() -> None:
    probe = load_probe("camera_share_probe14b_dshow")
    peers: dict[str, object] = {}
    shared = SharedCamera()

    class BreakingMsmf(FailedPeer):
        def isOpened(self) -> bool:  # noqa: N802 - OpenCV-compatible boundary
            shared.backend_name = "exclusive"
            return super().isOpened()

    def cv_factory(_index: int, backend: int):
        if backend == probe.cv2.CAP_MSMF:
            peers["msmf"] = BreakingMsmf(OSError("injected MSMF failure"))
            return peers["msmf"]
        peers["dshow"] = ContinuousPeer()
        return peers["dshow"]

    def exclusive_factory(_index: int):
        peers["exclusive"] = FailedPeer(PermissionError("injected WinRT failure"))
        return peers["exclusive"]

    result = probe.run_probe(
        index=2,
        seconds=1.0,
        shared_factory=lambda _index: shared,
        cv_factory=cv_factory,
        exclusive_factory=exclusive_factory,
        clock=TickClock(),
    )

    assert result["shareable"] is True
    assert result["msmf_error"] == "OSError"
    assert result["dshow_error"] is None
    assert result["winrt_exclusive_error"] == "PermissionError"
    assert result["dshow_continuous"] is True
    assert result["shared_during_dshow_continuous"] is True
    assert result["shared_backend_after_dshow"] == "shared"
    assert result["shared_backend_after_msmf"] == "exclusive"
    assert result["shared_backend_final"] == "exclusive"
    assert set(peers) == {"msmf", "dshow", "exclusive"}
    assert all(peer.released for peer in peers.values())


def test_dshow_failure_does_not_skip_winrt_exclusive_success() -> None:
    probe = load_probe("camera_share_probe14b_exclusive")
    exclusive = ContinuousPeer()

    result = probe.run_probe(
        index=3,
        seconds=1.0,
        shared_factory=lambda _index: SharedCamera(),
        cv_factory=lambda _index, backend: (
            ContinuousPeer()
            if backend == probe.cv2.CAP_MSMF
            else FailedPeer(RuntimeError("injected DSHOW failure"))
        ),
        exclusive_factory=lambda selected_index: exclusive if selected_index == 3 else None,
        clock=TickClock(),
    )

    assert result["shareable"] is True
    assert result["dshow_error"] == "RuntimeError"
    assert result["winrt_exclusive_error"] is None
    assert result["winrt_exclusive_continuous"] is True
    assert result["shared_during_winrt_exclusive_continuous"] is True
    assert result["shared_backend_after_winrt_exclusive"] == "shared"
    assert exclusive.released


def test_msmf_success_cannot_hide_both_primary_peer_failures() -> None:
    probe = load_probe("camera_share_probe14b_no_primary")

    result = probe.run_probe(
        index=0,
        seconds=1.0,
        shared_factory=lambda _index: SharedCamera(),
        cv_factory=lambda _index, backend: (
            ContinuousPeer()
            if backend == probe.cv2.CAP_MSMF
            else FailedPeer(OSError("injected DSHOW failure"))
        ),
        exclusive_factory=lambda _index: FailedPeer(PermissionError("injected exclusive failure")),
        clock=TickClock(),
    )

    assert result["msmf_continuous"] is True
    assert result["shared_during_msmf_continuous"] is True
    assert result["dshow_error"] == "OSError"
    assert result["winrt_exclusive_error"] == "PermissionError"
    assert result["shareable"] is False


def test_peer_frames_do_not_count_after_shared_backend_is_no_longer_shared() -> None:
    probe = load_probe("camera_share_probe14b_backend")

    class BackendSwitch(SharedCamera):
        def latest(self, after_seq: int, timeout: float):
            value = super().latest(after_seq, timeout)
            if self.seq >= 2:
                self.backend_name = "exclusive"
            return value

    result = probe.run_probe(
        index=0,
        seconds=1.0,
        shared_factory=lambda _index: BackendSwitch(),
        cv_factory=lambda _index, _backend: ContinuousPeer(),
        exclusive_factory=lambda _index: ContinuousPeer(),
        clock=TickClock(),
    )

    assert result["dshow_continuous"] is True
    assert result["winrt_exclusive_continuous"] is True
    assert result["shared_backend_final"] == "exclusive"
    assert result["shareable"] is False


def test_winrt_exclusive_worker_uses_same_index_and_warms_up_before_sampling() -> None:
    probe = load_probe("camera_share_probe14b_worker_ready")
    created: list[object] = []
    frame = np.full((2, 3, 3), 17, np.uint8)

    class FakeAdapter:
        def __init__(self, index, width, height, *, sharing_mode):
            self.args = (index, width, height, sharing_mode)
            self.reads = 0
            self.released = False
            created.append(self)

        def open(self) -> None:
            pass

        def read(self):
            self.reads += 1
            return (False, None) if self.reads == 1 else (True, frame)

        def release(self) -> None:
            self.released = True

    peer = probe._winrt_exclusive_peer(
        4,
        capture_factory=FakeAdapter,
        ready_timeout=0.5,
    )
    try:
        assert peer.isOpened()
        ok, actual = peer.read()
        assert ok and np.array_equal(actual, frame)
    finally:
        peer.release()

    assert len(created) == 1
    adapter = created[0]
    assert adapter.args == (4, 1280, 720, "exclusive")
    assert adapter.released


def test_winrt_exclusive_warmup_timeout_is_reported_and_released() -> None:
    probe = load_probe("camera_share_probe14b_worker_timeout")
    created: list[object] = []

    class SilentAdapter:
        def __init__(self, _index, _width, _height, *, sharing_mode):
            assert sharing_mode == "exclusive"
            self.released = False
            created.append(self)

        def open(self) -> None:
            pass

        def read(self):
            return False, None

        def release(self) -> None:
            self.released = True

    peer = probe._winrt_exclusive_peer(
        0,
        capture_factory=SilentAdapter,
        ready_timeout=0.02,
    )
    result = probe.run_probe(
        index=0,
        seconds=0.1,
        shared_factory=lambda _index: SharedCamera(),
        cv_factory=lambda _index, _backend: FailedPeer(OSError("injected OpenCV failure")),
        exclusive_factory=lambda _index: peer,
        clock=TickClock(),
    )

    assert result["winrt_exclusive_error"] == "TimeoutError"
    assert result["shareable"] is False
    assert len(created) == 1
    assert created[0].released
