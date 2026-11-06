"""Probe whether WinRT SharedReadOnly keeps flowing beside competing camera users.

The camera index is the WinRT video-device-list index.  Windows does not
guarantee that DirectShow or MSMF use the same ordering, so verify the selected
device visually when running this diagnostic manually.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from desk_sentry.camera import Camera  # noqa: E402
from desk_sentry.camera_shared import SharedCameraCapture  # noqa: E402

_PEER_READY_TIMEOUT = 10.0


def _opencv_capture(index: int, backend: int) -> Any:
    return cv2.VideoCapture(index, backend)


def _probe_camera(
    index: int,
    shared_capture_factory: Callable[[int, int, int], Any] = SharedCameraCapture,
) -> Camera:
    return Camera(
        index,
        mode="auto",
        shared_factory=shared_capture_factory,
        # The probe must observe a shared failure, never hide it by opening DSHOW.
        capture_factory=lambda: None,
        # A missing fake exclusive capture must end the worker, not retry forever.
        retry_wait=lambda _seconds: True,
    )


class _CameraPeer:
    """Expose a Camera worker through the small OpenCV-like probe contract."""

    def __init__(self, camera: Camera, *, ready_timeout: float = _PEER_READY_TIMEOUT) -> None:
        self._camera = camera
        self._seq = 0
        self._started = False
        self._ready_timeout = ready_timeout
        self._ready_frame: Any | None = None

    @property
    def failure_reason(self) -> str | None:
        return self._camera.failure_reason

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV-compatible boundary
        if not self._started:
            self._camera.start()
            self._started = True
        if self._ready_frame is not None:
            return True
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            next_seq, frame = self._camera.latest(self._seq, min(0.05, remaining))
            if next_seq > self._seq and frame is not None:
                self._seq = next_seq
                self._ready_frame = frame
                return True
            if self._camera.backend_name == "exclusive" and self._camera.failure_reason:
                return False
        raise TimeoutError("WinRT exclusive peer produced no frame during warmup")

    def read(self) -> tuple[bool, Any | None]:
        if self._ready_frame is not None:
            frame, self._ready_frame = self._ready_frame, None
            return True, frame
        next_seq, frame = self._camera.latest(self._seq, 0.05)
        if next_seq <= self._seq or frame is None:
            return False, None
        self._seq = next_seq
        return True, frame

    def release(self) -> None:
        if self._started:
            self._camera.stop()


def _winrt_exclusive_peer(
    index: int,
    capture_factory: Callable[..., Any] = SharedCameraCapture,
    *,
    ready_timeout: float = _PEER_READY_TIMEOUT,
) -> _CameraPeer:
    def exclusive(selected: int, width: int, height: int) -> Any:
        return capture_factory(
            selected,
            width,
            height,
            sharing_mode="exclusive",
        )

    return _CameraPeer(
        Camera(
            index,
            mode="auto",
            shared_factory=exclusive,
            capture_factory=lambda: None,
            retry_wait=lambda _seconds: True,
        ),
        ready_timeout=ready_timeout,
    )


def _observation(
    count: int, first_at: float | None, last_at: float | None, seconds: float
) -> dict[str, object]:
    span = last_at - first_at if first_at is not None and last_at is not None else 0.0
    return {
        "frames": count,
        "first_s": round(first_at, 3) if first_at is not None else None,
        "last_s": round(last_at, 3) if last_at is not None else None,
        "span_s": round(span, 3),
        "continuous": count >= 2 and span >= seconds / 2,
    }


def _count_pair(
    camera: Any,
    capture: Any,
    seq: int,
    seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, object], dict[str, object]]:
    shared_count = peer_count = 0
    shared_first = shared_last = peer_first = peer_last = None
    started = clock()
    deadline = started + seconds
    while clock() < deadline:
        ok, frame = capture.read()
        now = clock()
        if now <= deadline and ok and frame is not None and frame.size:
            peer_count += 1
            peer_first = now - started if peer_first is None else peer_first
            peer_last = now - started
        next_seq, shared = camera.latest(seq, min(0.05, seconds))
        now = clock()
        if now <= deadline and next_seq > seq and shared is not None:
            shared_count += 1
            seq = next_seq
            shared_first = now - started if shared_first is None else shared_first
            shared_last = now - started
    return (
        seq,
        _observation(shared_count, shared_first, shared_last, seconds),
        _observation(peer_count, peer_first, peer_last, seconds),
    )


def _store_observation(
    result: dict[str, object], prefix: str, observation: dict[str, object]
) -> None:
    for field, value in observation.items():
        result[f"{prefix}_{field}"] = value


def run_probe(
    *,
    index: int,
    seconds: float,
    shared_factory: Callable[[int], Any] | None = None,
    shared_capture_factory: Callable[[int, int, int], Any] = SharedCameraCapture,
    cv_factory: Callable[[int, int], Any] = _opencv_capture,
    exclusive_factory: Callable[[int], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Run the probe; factories make the complete control flow unit-testable."""

    camera = (
        shared_factory(index)
        if shared_factory is not None
        else _probe_camera(index, shared_capture_factory)
    )
    result: dict[str, object] = {
        "index": index,
        "seconds_per_backend": seconds,
        "shared_backend": "pending",
        "shared_failure": None,
        "shared_backend_final": "pending",
        "shared_failure_final": None,
        "shared_warmup_frames": 0,
        "shared_frames": 0,
        "shareable": False,
        "index_order_note": "WinRT order is not guaranteed to match OpenCV/DirectShow",
    }
    peer_names = ("msmf", "dshow", "winrt_exclusive")
    for prefix in (
        "msmf",
        "shared_during_msmf",
        "dshow",
        "shared_during_dshow",
        "winrt_exclusive",
        "shared_during_winrt_exclusive",
    ):
        _store_observation(result, prefix, _observation(0, None, None, seconds))
    for name in peer_names:
        result[f"{name}_error"] = None
        result[f"shared_backend_after_{name}"] = "pending"
        result[f"shared_failure_after_{name}"] = None
    seq = 0
    camera.start()
    try:
        # Prove the requested path is genuinely shared before opening competitors.
        warmup_deadline = clock() + max(1.0, seconds)
        warmup_frames = 0
        while clock() < warmup_deadline:
            next_seq, frame = camera.latest(seq, 0.05)
            if next_seq > seq and frame is not None:
                seq = next_seq
                warmup_frames += 1
            if camera.backend_name != "pending" and (
                camera.backend_name != "shared" or warmup_frames
            ):
                break
        result["shared_backend"] = camera.backend_name
        result["shared_failure"] = camera.failure_reason
        result["shared_warmup_frames"] = warmup_frames
        if camera.backend_name != "shared":
            return result

        total_shared = 0
        peers: tuple[tuple[str, Callable[[], Any]], ...] = (
            ("dshow", lambda: cv_factory(index, cv2.CAP_DSHOW)),
            (
                "winrt_exclusive",
                lambda: (
                    exclusive_factory(index)
                    if exclusive_factory is not None
                    else _winrt_exclusive_peer(index)
                ),
            ),
            # MSMF is diagnostic-only and runs last so it cannot prevent either
            # principal pairing from being sampled on a still-shared camera.
            ("msmf", lambda: cv_factory(index, cv2.CAP_MSMF)),
        )
        for name, make_capture in peers:
            capture = None
            error: str | None = None
            try:
                capture = make_capture()
                if not capture.isOpened():
                    failure = getattr(capture, "failure_reason", None)
                    error = failure if isinstance(failure, str) and failure else "OpenError"
                else:
                    seq, shared_observation, peer_observation = _count_pair(
                        camera, capture, seq, seconds, clock=clock
                    )
                    shared_frames = shared_observation["frames"]
                    if isinstance(shared_frames, int):
                        total_shared += shared_frames
                    _store_observation(result, name, peer_observation)
                    _store_observation(result, f"shared_during_{name}", shared_observation)
                    failure = getattr(capture, "failure_reason", None)
                    if isinstance(failure, str) and failure:
                        error = failure
            except Exception as exc:  # noqa: BLE001 - report one peer and continue to the next
                error = type(exc).__name__
            finally:
                if capture is not None:
                    try:
                        capture.release()
                    except Exception as exc:  # noqa: BLE001 - result contains type only
                        error = error or type(exc).__name__
                result[f"{name}_error"] = error
                result[f"shared_backend_after_{name}"] = camera.backend_name
                result[f"shared_failure_after_{name}"] = camera.failure_reason
        result["shared_frames"] = total_shared
        result["shared_backend_final"] = camera.backend_name
        result["shared_failure_final"] = camera.failure_reason
        result["shareable"] = any(
            result[f"{name}_error"] is None
            and bool(result[f"{name}_continuous"])
            and bool(result[f"shared_during_{name}_continuous"])
            and result[f"shared_backend_after_{name}"] == "shared"
            for name in ("dshow", "winrt_exclusive")
        )
        return result
    finally:
        result["shared_backend_final"] = camera.backend_name
        result["shared_failure_final"] = camera.failure_reason
        camera.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")
    try:
        result = run_probe(index=args.index, seconds=args.seconds)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["shareable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
