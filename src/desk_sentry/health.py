"""Numbers-only engine health and bounded camera retry scheduling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

NO_FRAME_SECONDS = 3.0
SLOW_INFERENCE_SECONDS = 2.0
SLOW_INFERENCE_COUNT = 3
RECONNECT_DELAYS = (2.0, 5.0, 10.0, 30.0)


class HealthState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class HealthTransition:
    state: HealthState
    reason: str
    duration_s: float


class HealthMonitor:
    """Combine frame, inference and error signals without Qt or camera access."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._active = False
        self._last_frame = clock()
        self._slow_count = 0
        self._error: str | None = None
        self._degraded_since: float | None = None
        self.state = HealthState.OK
        self.reason = ""

    def resume(self) -> None:
        if not self._active:
            self._last_frame = self._clock()
        self._active = True

    def pause(self) -> None:
        self._active = False
        self._slow_count = 0
        self._error = None

    def frame(self, when: float | None = None) -> None:
        self._last_frame = self._clock() if when is None else when

    def inference(self, duration_s: float) -> None:
        self._slow_count = self._slow_count + 1 if duration_s > SLOW_INFERENCE_SECONDS else 0
        self._error = None

    def error(self, reason: str) -> None:
        self._error = reason

    def check(self) -> HealthTransition | None:
        now = self._clock()
        reason = ""
        if self._active:
            if self._error is not None:
                reason = self._error
            elif now - self._last_frame >= NO_FRAME_SECONDS:
                reason = "no_frame"
            elif self._slow_count >= SLOW_INFERENCE_COUNT:
                reason = "slow_inference"
        state = HealthState.DEGRADED if reason else HealthState.OK
        if state is self.state:
            self.reason = reason
            return None
        if state is HealthState.DEGRADED:
            self._degraded_since = now
            duration = 0.0
        else:
            duration = now - self._degraded_since if self._degraded_since is not None else 0.0
            self._degraded_since = None
        self.state, self.reason = state, reason
        return HealthTransition(state, reason or "recovered", duration)


class ReconnectBackoff:
    """Escalate retries during one frame outage; a valid frame resets the episode."""

    def __init__(self) -> None:
        self._failures = 0

    def failed(self) -> float:
        delay = RECONNECT_DELAYS[min(self._failures, len(RECONNECT_DELAYS) - 1)]
        self._failures += 1
        return delay

    def recovered(self) -> None:
        self._failures = 0
