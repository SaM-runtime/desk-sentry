"""Camera → detector → tracker → alert pipeline on one background thread."""

from __future__ import annotations

import contextlib
import math
import re
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from desk_sentry.actions import EVADE_NEAR_HEIGHT_MULTIPLIER
from desk_sentry.camera import Camera, Image
from desk_sentry.config import SENSITIVITY, Settings
from desk_sentry.detector import Box, Detection
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.faces import FaceEmbedder
from desk_sentry.health import HealthMonitor, HealthState
from desk_sentry.motion import MotionMap
from desk_sentry.people import Level, Match, PeopleSnapshot, Vector
from desk_sentry.sentry import AlertEngine, Assessment, Policy, Role, Status, Zone
from desk_sentry.tracker import Track, Tracker, boxes_overlap

ACTIVE_INTERVAL = 0.1  # ~10 Hz while something moves or someone is tracked
IDLE_INTERVAL = 0.5  # quiet empty scene: 2 Hz is enough and saves CPU
STATUS_ONLY_INTERVAL = 0.2  # snapshot rate when nobody is looking at the preview


def _diagnostic_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if "desk_sentry" in parts:
        return "/".join(parts[parts.index("desk_sentry") :])
    return parts[-1] or "<path>"


def _diagnostic_trace(exc: Exception) -> str:
    # Do not include source lines or locals: either may contain private paths.
    frames = traceback.extract_tb(exc.__traceback__, limit=8)
    lines = [f"{_diagnostic_path(f.filename)}:{f.lineno} in {f.name}" for f in frames]
    message = re.sub(
        r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n\"'<>]*",
        lambda match: _diagnostic_path(match.group()),
        str(exc),
    )
    lines.append(f"{type(exc).__name__}: {message}")
    return "\n".join(lines)


def _diagnostic_token(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,95}", value)
        else None
    )


THUMB_MAX_SIZE = (240, 160)  # width, height: 10 history thumbnails stay under 1 MB
THUMB_MIN_INTERVAL = 3.0
_THUMB_PADDING = 0.2
ERROR_RETRY_SECONDS = 2.0
CAMERA_FATAL_RETRY_INITIAL = 30.0
CAMERA_FATAL_RETRY_MAX = 300.0
_PREVIEW_MAX_WIDTH = 960
# Face work per analysis step is bounded; recognised people are re-checked
# rarely, unrecognised ones often (they may turn towards the camera).
FACES_PER_STEP = 2
RECHECK_KNOWN_SECONDS = 2.0
RECHECK_UNKNOWN_SECONDS = 0.3
# An alert thumbnail without a usable face is replaced if the same person shows
# their face within this long.
FACE_UPGRADE_SECONDS = 5.0
APPROACHING_DISPLAY_HOLD_SECONDS = 1.5
WATCHING_DISPLAY_HOLD_SECONDS = 1.0


class StatusSmoother:
    """Hold only the two noisy UI downgrades; raw alert decisions stay separate."""

    def __init__(self) -> None:
        self._last_approaching: float | None = None
        self._last_watching: float | None = None

    def update(self, status: Status, now: float) -> Status:
        if status is Status.APPROACHING:
            self._last_approaching = now
        elif status is not Status.ALERT:
            self._last_approaching = None
        if status is Status.WATCHING:
            self._last_watching = now
        elif status is not Status.CLEAR:
            self._last_watching = None
        return self.display(status, now)

    def display(self, status: Status, now: float) -> Status:
        """Render cached analysis without treating a preview frame as a new observation."""

        if (
            status is Status.ALERT
            and self._last_approaching is not None
            and now - self._last_approaching < APPROACHING_DISPLAY_HOLD_SECONDS
        ):
            return Status.APPROACHING
        if (
            status is Status.CLEAR
            and self._last_watching is not None
            and now - self._last_watching < WATCHING_DISPLAY_HOLD_SECONDS
        ):
            return Status.WATCHING
        return status


class Detector(Protocol):
    def detect(self, frame: Image, score_threshold: float = ...) -> list[Detection]: ...


class CameraSource(Protocol):
    index: int
    connected: bool
    fps: float
    nominal_fps: float

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self, after_seq: int, timeout: float) -> tuple[int, Image | None]: ...


@dataclass(frozen=True, slots=True)
class TrackView:
    track_id: int
    box: tuple[float, float, float, float]  # normalized x1, y1, x2, y2
    role: Role
    score: float
    name: str | None = None  # labelled person recognised on this track
    seen: bool = True  # detected in the latest analysis frame


@dataclass(slots=True)
class _Identity:
    match: Match | None = None
    last_try: float = float("-inf")
    face: Vector | None = None  # best (largest) face feature seen on this track
    face_size: float = 0.0


@dataclass(frozen=True, slots=True)
class Snapshot:
    status: Status
    frame: NDArray[np.uint8] | None = None  # RGB preview, RAM only
    tracks: tuple[TrackView, ...] = ()
    self_zone: Zone | None = None
    intruders: int = 0
    people: int = 0
    alert_started: bool = False
    continued: bool = False
    # Small mirrored RGB crop of who triggered the alert. RAM only: never written
    # to disk or logged; the UI keeps the last few and drops them on exit.
    alert_thumbnail: NDArray[np.uint8] | None = None
    alert_shot_id: int = 0  # replaces the history item with this id if it exists
    alert_face: Vector | None = None  # face feature, for labelling who it was
    alert_name: str | None = None
    alert_level: Level | None = None  # strongest level of the people alerting
    level_raised: bool = False
    camera_fps: float = 0.0
    nominal_fps: float = 0.0
    analysis_fps: float = 0.0
    latency_ms: float = 0.0
    raw_status: Status | None = None
    health_state: HealthState = HealthState.OK
    health_reason: str | None = None
    evade_near: bool = False
    alert_ts: float | None = None
    camera_backend: str = "exclusive"
    backend_detail: str | None = None
    subtype: str | None = None
    width: int = 0
    height: int = 0
    last_camera_error: str | None = None
    # Acknowledged only after the camera's stop/release has completed.
    pause_reason: str | None = None
    pause_request: int = 0


SnapshotCallback = Callable[[Snapshot], None]


_DISPLAY_ROLE_PRIORITY = {
    Role.FAR: 0,
    Role.PENDING: 1,
    Role.PASSING: 2,
    Role.STILL: 3,
    Role.KNOWN: 4,
    Role.INTRUDER: 6,
    Role.APPROACHING: 7,
}
_SELF_HIDDEN_ROLES = {Role.PENDING, Role.STILL, Role.PASSING, Role.FAR, Role.KNOWN}


def display_tracks(views: Iterable[TrackView]) -> tuple[TrackView, ...]:
    """One box and status card per person on screen.

    The detector often returns two boxes for one person (upper body inside full
    body) and a lost track lingers next to its replacement, so several cards
    stacked on one person. Alerting still sees every track; only the view is
    thinned, like non-maximum suppression ranked by how much a role matters.
    """

    items = tuple(views)
    boxes = [Box(*view.box) for view in items]
    selves = [boxes[i] for i, view in enumerate(items) if view.role is Role.SELF]
    keep = {i for i, view in enumerate(items) if view.role is Role.SELF}
    kept: list[Box] = []
    others = [i for i, view in enumerate(items) if view.role is not Role.SELF]
    for i in sorted(others, key=lambda i: _display_priority(items[i]), reverse=True):
        view = items[i]
        if view.role is Role.PENDING and not view.seen:
            continue
        # The user's own body often yields a second box; never hide a moving
        # person or someone the user labelled, even right behind the seat.
        if (
            view.role in _SELF_HIDDEN_ROLES
            and view.name is None
            and any(boxes_overlap(boxes[i], own) for own in selves)
        ):
            continue
        if any(boxes_overlap(boxes[i], other) for other in kept):
            continue
        kept.append(boxes[i])
        keep.add(i)
    return tuple(view for i, view in enumerate(items) if i in keep)


def _display_priority(view: TrackView) -> tuple[int, float]:
    priority = _DISPLAY_ROLE_PRIORITY[view.role]
    if view.name is not None and view.role not in (Role.APPROACHING, Role.INTRUDER):
        priority = 5
    return priority, view.score


class Engine:
    def __init__(
        self,
        detector: Detector,
        settings: Settings,
        on_snapshot: SnapshotCallback,
        on_zone_calibrated: Callable[[Zone], None] | None = None,
        *,
        camera_factory: Callable[[int], CameraSource] | None = None,
        faces: FaceEmbedder | None = None,
        faces_loader: Callable[[], FaceEmbedder | None] | None = None,
    ) -> None:
        self._detector = detector
        # Face models cost ~60 MB, so they load on first real need (an alert to
        # label, or someone labelled) rather than at start-up.
        self._faces = faces
        self._faces_loader = faces_loader
        self._people = PeopleSnapshot()
        self._on_snapshot = on_snapshot
        self._on_zone = on_zone_calibrated
        self._camera_factory = camera_factory
        self._camera_mode = settings.camera_mode
        self._camera_id = settings.camera_id
        self._camera_name = settings.camera_name
        self._camera_backend = "pending" if settings.camera_mode == "auto" else "exclusive"
        self._camera_backend_detail: str | None = None
        self._camera_subtype: str | None = None
        self._camera_width = 0
        self._camera_height = 0
        self._camera_fps = 0.0
        self._camera_nominal_fps = 0.0
        self._last_camera_error: str | None = None
        self._reported_backend: tuple[str, str | None, str | None] | None = None
        self._reported_formats: set[tuple[str | None, int, int]] = set()
        self._exclusive_allowed = threading.Event()
        self._exclusive_allowed.set()
        self._camera_unresponsive = False
        self._camera_release_failed = False
        self._camera_retry_at = 0.0
        self._camera_retry_delay = CAMERA_FATAL_RETRY_INITIAL
        self._discarded_camera_identity: tuple[int, str, str, str] | None = None
        self._lock = threading.Lock()
        self._settings = settings
        self._pause_reason = "manual"
        self._current_pause_reason = "manual"
        self._pause_request = 0
        self._current_pause_request = 0
        self._dirty = True
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._camera: CameraSource | None = None
        self._camera_closing = False
        self._tracker = Tracker()
        self._alerts = AlertEngine()
        # Converting frames for display costs CPU; skip it while nothing shows them.
        self.preview_wanted = True
        self.error_count = 0
        # Opt-in numbers-only log; swapped from the UI thread, read once per step.
        self.diagnostics: DiagnosticsLog | None = None
        self.health = HealthMonitor(clock=lambda: time.monotonic())

    def _update_health(self) -> None:
        frame_at = getattr(self._camera, "last_frame_at", None)
        if isinstance(frame_at, (int, float)):
            self.health.frame(frame_at)
        transition = self.health.check()
        if transition is not None and self.diagnostics is not None:
            self.diagnostics.write(
                "health",
                state=transition.state.value,
                reason=transition.reason,
                duration_s=round(transition.duration_s, 3),
            )

    def _emit_snapshot(self, snapshot: Snapshot) -> None:
        self._update_health()
        if self._camera is not None:
            self._capture_camera_metadata(self._camera)
            failure = _diagnostic_token(getattr(self._camera, "failure_reason", None))
            if failure is not None:
                self._last_camera_error = failure
            record = (self._camera_backend, self._camera_backend_detail, failure)
            if self.diagnostics is not None and record != self._reported_backend:
                self.diagnostics.write(
                    "camera_backend",
                    backend=self._camera_backend,
                    backend_detail=self._camera_backend_detail,
                    subtype=self._camera_subtype,
                    width=self._camera_width,
                    height=self._camera_height,
                    nominal_fps=round(self._camera_nominal_fps, 1),
                    failure_reason=failure,
                )
                self._reported_backend = record
            format_key = (
                self._camera_subtype,
                self._camera_width,
                self._camera_height,
            )
            if (
                self.diagnostics is not None
                and self._camera_subtype is not None
                and self._camera_width > 0
                and self._camera_height > 0
                and format_key not in self._reported_formats
            ):
                self.diagnostics.write(
                    "camera_format",
                    backend=self._camera_backend,
                    backend_detail=self._camera_backend_detail,
                    subtype=self._camera_subtype,
                    width=self._camera_width,
                    height=self._camera_height,
                    nominal_fps=round(self._camera_nominal_fps, 1),
                )
                self._reported_formats.add(format_key)
        health_state = HealthState.DEGRADED if self._camera_unresponsive else self.health.state
        health_reason = "camera_unresponsive" if self._camera_unresponsive else self.health.reason
        if self._camera_release_failed:
            health_state = HealthState.DEGRADED
            health_reason = "camera_release_failed"
            if snapshot.status is Status.PAUSED:
                snapshot = replace(snapshot, pause_reason=None, pause_request=0)
        self._on_snapshot(
            replace(
                snapshot,
                health_state=health_state,
                health_reason=health_reason or None,
                camera_backend=self._camera_backend,
                backend_detail=self._camera_backend_detail,
                subtype=self._camera_subtype,
                width=self._camera_width,
                height=self._camera_height,
                camera_fps=self._camera_fps,
                nominal_fps=self._camera_nominal_fps,
                last_camera_error=self._last_camera_error,
            )
        )

    def _capture_camera_metadata(self, camera: CameraSource) -> None:
        backend = getattr(camera, "backend_name", "exclusive")
        self._camera_backend = (
            backend if backend in ("shared", "exclusive", "yielded", "pending") else "pending"
        )
        detail = _diagnostic_token(getattr(camera, "backend_detail", None))
        self._camera_backend_detail = detail if detail in ("winrt", "dshow") else None
        self._camera_subtype = _diagnostic_token(getattr(camera, "subtype", None))
        width = getattr(camera, "width", 0)
        height = getattr(camera, "height", 0)
        fps = getattr(camera, "fps", 0.0)
        nominal_fps = getattr(camera, "nominal_fps", 0.0)
        self._camera_width = width if type(width) is int and width > 0 else 0
        self._camera_height = height if type(height) is int and height > 0 else 0
        self._camera_fps = (
            float(fps)
            if isinstance(fps, (int, float))
            and not isinstance(fps, bool)
            and math.isfinite(fps)
            and fps >= 0
            else 0.0
        )
        self._camera_nominal_fps = (
            float(nominal_fps)
            if isinstance(nominal_fps, (int, float))
            and not isinstance(nominal_fps, bool)
            and math.isfinite(nominal_fps)
            and nominal_fps >= 0
            else 0.0
        )

    def _record_error(self, exc: Exception) -> None:
        self.error_count += 1
        self.health.error(type(exc).__name__)
        if self.diagnostics is not None:
            self.diagnostics.write("error", error=type(exc).__name__, trace=_diagnostic_trace(exc))

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._close_camera()
        if self.diagnostics is not None:
            self.diagnostics.flush()  # buffered records must not be lost on exit

    def _face_embedder(self) -> FaceEmbedder | None:
        if self._faces is None and self._faces_loader is not None:
            self._faces = self._faces_loader()
            self._faces_loader = None  # try once
        return self._faces

    def set_people(self, people: PeopleSnapshot) -> None:
        self._people = people  # immutable snapshot; a reference swap is atomic

    def set_exclusive_allowed(self, allowed: bool) -> None:
        """Allow Camera's compatibility fallback without sharing mutable UI state."""

        if allowed:
            self._exclusive_allowed.set()
        else:
            self._exclusive_allowed.clear()
        self._wake.set()

    def apply(self, settings: Settings, *, pause_reason: str = "manual") -> int:
        with self._lock:
            self._settings = settings
            self._pause_reason = (
                pause_reason
                if pause_reason in ("manual", "locked", "schedule", "meeting", "manual_yield")
                else "manual"
            )
            self._dirty = True
            self._pause_request += 1
            request = self._pause_request
        self._wake.set()
        return request

    def _close_camera(self) -> None:
        if self._camera is not None:
            self._camera_closing = True
            self._capture_camera_metadata(self._camera)
            fatal_error = _diagnostic_token(getattr(self._camera, "fatal_error", None))
            worker_alive = getattr(self._camera, "worker_alive", True)
            try:
                if fatal_error is None or worker_alive is not False:
                    self._camera.stop()
                else:
                    self._last_camera_error = fatal_error
            except Exception:
                # An ended reader with a failed release cannot acknowledge a
                # handoff. A still-running operation retains the retry path.
                if (
                    getattr(self._camera, "release_failed", False) is not True
                    or getattr(self._camera, "worker_alive", True) is not False
                ):
                    raise
            self._remember_release_failure(self._camera)
            self._camera = None
        self._camera_closing = False
        self._tracker.reset()
        self._alerts.reset()

    def _remember_release_failure(self, camera: CameraSource) -> None:
        if getattr(camera, "release_failed", False) is True:
            self._camera_release_failed = True
            self._last_camera_error = (
                _diagnostic_token(getattr(camera, "failure_reason", None))
                or _diagnostic_token(getattr(camera, "fatal_error", None))
                or "CameraReleaseError"
            )

    def _sync_settings(self) -> Settings:
        with self._lock:
            settings, dirty, self._dirty = self._settings, self._dirty, False
            self._current_pause_reason = self._pause_reason
            self._current_pause_request = self._pause_request
        if dirty:
            if self._alerts.policy.self_zone != settings.self_zone:
                self._alerts.reset()
            self._alerts.policy = Policy(
                sensitivity=settings.sensitivity,
                exclude_self=settings.exclude_self,
                self_zone=settings.self_zone,
                stationary_mute_seconds=settings.stationary_mute_seconds,
                alert_far_passersby=settings.alert_far_passersby,
                unknown_level=Level(settings.unknown_level),
            )
            if self._camera is not None and (
                self._camera.index != settings.camera_index
                or self._camera_mode != settings.camera_mode
                or self._camera_id != settings.camera_id
                or self._camera_name != settings.camera_name
            ):
                self._close_camera()
        return settings

    def _recognise(
        self,
        frame: Image,
        tracks: list[Track],
        now: float,
        height: int,
        settings: Settings,
        state: _LoopState,
    ) -> tuple[dict[int, Level], set[int]]:
        """Face recognition for the few tracks that need it; nothing when idle."""

        live = {t.track_id for t in tracks}
        state.ids = {k: v for k, v in state.ids.items() if k in live}
        state.face_wanted = {k: v for k, v in state.face_wanted.items() if k in live and v[1] > now}
        people = self._people
        if people.empty and not state.face_wanted:
            return {}, set()
        faces = self._face_embedder()
        if faces is None:
            return {}, set()
        min_height = SENSITIVITY[settings.sensitivity][1]
        candidates = _candidates(tracks, now, height, min_height, state)
        _recognise_tracks(faces, people, frame, tracks, now, candidates, state)
        identities = {
            tid: ident.match.level for tid, ident in state.ids.items() if ident.match is not None
        }
        awaiting = (
            {t.track_id for t in candidates if t.track_id not in identities}
            if people.has_quiet_people
            else set()
        )
        return identities, awaiting

    def _run(self) -> None:
        state = _LoopState()
        while not self._stop.is_set():
            try:
                self._step(state)
            except MemoryError as exc:
                state.last_detect = float("-inf")
                self._record_error(exc)
                self._emit_snapshot(Snapshot(Status.ERROR))
            except Exception as exc:  # noqa: BLE001 - keep the sentry alive, report, retry
                self._record_error(exc)
                # Preserve ownership until stop confirms release. A stuck
                # driver must not kill the worker or allow a second open.
                with contextlib.suppress(Exception):
                    self._close_camera()
                state.reset()
                self._emit_snapshot(Snapshot(Status.ERROR))
                self._stop.wait(ERROR_RETRY_SECONDS)

    def _discard_fatal_camera(self, state: _LoopState, error: str, now: float) -> None:
        camera = self._camera
        if camera is not None:
            self._capture_camera_metadata(camera)
            self._remember_release_failure(camera)
            self._discarded_camera_identity = (
                camera.index,
                self._camera_id,
                self._camera_name,
                self._camera_mode,
            )
        self._camera = None
        self._camera_closing = False
        self._tracker.reset()
        self._alerts.reset()
        state.reset()
        self.error_count += 1
        self._last_camera_error = error
        self._camera_unresponsive = True
        self._camera_retry_at = now + self._camera_retry_delay
        self._camera_retry_delay = min(self._camera_retry_delay * 2.0, CAMERA_FATAL_RETRY_MAX)
        if self.diagnostics is not None:
            # A fatal camera record contains only the exception type. Driver
            # messages can include device identifiers or private paths.
            self.diagnostics.write("error", error=error)
        self._emit_snapshot(Snapshot(Status.NO_CAMERA))
        state.last_emit = now

    def _step(self, state: _LoopState) -> None:
        settings = self._sync_settings()
        if self._camera_closing:
            self._close_camera()
        log = self.diagnostics
        if not settings.armed:
            self.health.pause()
            if self._camera is not None:
                self._close_camera()
                if log is not None:
                    log.write("paused", reason=self._current_pause_reason)
            self._emit_snapshot(
                Snapshot(
                    state.status_smoother.update(Status.PAUSED, time.monotonic()),
                    pause_reason=self._current_pause_reason,
                    pause_request=self._current_pause_request,
                )
            )
            self._wake.wait(1.0)
            self._wake.clear()
            return

        self.health.resume()

        if self._camera is None:
            now = time.monotonic()
            camera_identity = (
                settings.camera_index,
                settings.camera_id,
                settings.camera_name,
                settings.camera_mode,
            )
            if (
                self._camera_unresponsive
                and self._discarded_camera_identity is not None
                and camera_identity != self._discarded_camera_identity
            ):
                self._camera_unresponsive = False
                self._camera_retry_at = 0.0
                self._camera_retry_delay = CAMERA_FATAL_RETRY_INITIAL
                self._discarded_camera_identity = None
            if self._camera_unresponsive and now < self._camera_retry_at:
                if now - state.last_emit >= 0.5:
                    self._emit_snapshot(Snapshot(Status.NO_CAMERA))
                    state.last_emit = now
                self._wake.wait(min(1.0, self._camera_retry_at - now))
                self._wake.clear()
                return
            self._camera_mode = settings.camera_mode
            camera_id = settings.camera_id
            self._camera_id = camera_id
            camera_name = settings.camera_name
            self._camera_name = camera_name
            self._camera = (
                self._camera_factory(settings.camera_index)
                if self._camera_factory is not None
                else Camera(
                    settings.camera_index,
                    camera_id=camera_id,
                    camera_name=camera_name,
                    mode="exclusive" if settings.camera_mode == "exclusive" else "auto",
                    exclusive_allowed=self._exclusive_allowed.is_set,
                )
            )
            self._camera.start()
            state.reset()
            if log is not None:
                log.write(
                    "armed",
                    camera_index=settings.camera_index,
                    sensitivity=settings.sensitivity,
                    exclude_self=settings.exclude_self,
                    self_zone=settings.self_zone,
                    stationary_mute_seconds=settings.stationary_mute_seconds,
                )
        camera = self._camera
        fatal_error = _diagnostic_token(getattr(camera, "fatal_error", None))
        worker_alive = getattr(camera, "worker_alive", True)
        if fatal_error is not None and worker_alive is False:
            self._discard_fatal_camera(state, fatal_error, time.monotonic())
            return
        state.seq, frame = camera.latest(state.seq, timeout=0.5)
        now = time.monotonic()
        if frame is None:
            self._update_health()
            if (
                not camera.connected or self.health.state is HealthState.DEGRADED
            ) and now - state.last_emit >= 0.5:
                lost = not camera.connected or self.health.reason == "no_frame"
                if log is not None and state.camera_ok and lost:
                    log.write("camera_lost")
                if lost:
                    state.camera_ok = False
                if not camera.connected:
                    self._tracker.reset()
                    self._alerts.reset()
                status = (
                    Status.NO_CAMERA
                    if not camera.connected or self.health.reason == "no_frame"
                    else state.assessment.status
                )
                self._emit_snapshot(Snapshot(state.status_smoother.update(status, now)))
                state.last_emit = now
            return

        self._camera_release_failed = False
        if self._camera_unresponsive:
            self._camera_unresponsive = False
            self._camera_retry_at = 0.0
            self._camera_retry_delay = CAMERA_FATAL_RETRY_INITIAL
            self._discarded_camera_identity = None
        self.health.frame()

        if not state.camera_ok:
            state.camera_ok = True
            if log is not None:
                log.write("camera_ok", size=[frame.shape[1], frame.shape[0]])
        moved = state.motion.push(frame, now)
        busy = moved or bool(self._tracker.tracks) or self._alerts.alert_active
        interval = ACTIVE_INTERVAL if busy else IDLE_INTERVAL
        height, width = frame.shape[:2]
        assessment = state.assessment
        if now - state.last_detect >= interval:
            state.last_detect = now
            min_score = SENSITIVITY[settings.sensitivity][0]
            started = time.perf_counter()
            try:
                detections = self._detector.detect(frame, score_threshold=min_score * 0.8)
            except MemoryError as exc:
                state.last_detect = float("-inf")
                self._record_error(exc)
                self._emit_snapshot(Snapshot(Status.ERROR))
                return
            tracks = self._tracker.update(detections, now)
            identities, awaiting = self._recognise(frame, tracks, now, height, settings, state)
            assessment = state.assessment = self._alerts.assess(
                tracks,
                now,
                (width, height),
                motion=state.motion.fraction,
                identities=identities,
                awaiting=awaiting,
            )
            if assessment.alert_started:
                state.alert_ts = time.time()
            presentation_status = (
                Status.WATCHING
                if assessment.status is Status.NOTICE and not settings.discreet
                else assessment.status
            )
            state.status_smoother.update(presentation_status, now)
            state.latency_ms = (time.perf_counter() - started) * 1000.0
            self.health.inference(state.latency_ms / 1000.0)
            state.analysis_times = [t for t in state.analysis_times if now - t < 2.0] + [now]
            if log is not None:
                _log_analysis(
                    log,
                    assessment,
                    detections,
                    tracks,
                    now,
                    width,
                    height,
                    state,
                    camera.fps,
                    moved,
                    self._alerts.policy.self_zone,
                )
            if assessment.calibrated_zone is not None and self._on_zone is not None:
                self._on_zone(assessment.calibrated_zone)
            if (
                assessment.alert_started or assessment.continued
            ) and now - state.last_thumb >= THUMB_MIN_INTERVAL:
                alerting_tracks = [
                    t
                    for t in tracks
                    if assessment.roles.get(t.track_id) in (Role.INTRUDER, Role.APPROACHING)
                ]
                image = alert_thumbnail(frame, [t.box for t in alerting_tracks])
                if image is not None:
                    state.last_thumb = now
                    state.shot_counter += 1
                    main = max(alerting_tracks, key=lambda t: t.box.area)
                    ident = state.ids.get(main.track_id)
                    face = ident.face if ident else None
                    embedder = self._face_embedder()
                    if face is None and embedder is not None:
                        found = embedder.embed(frame, main.box)
                        face = found.embedding if found else None
                    if face is None and embedder is not None:
                        state.face_wanted[main.track_id] = (
                            state.shot_counter,
                            now + FACE_UPGRADE_SECONDS,
                        )
                    state.pending_shot = _Shot(
                        state.shot_counter,
                        image,
                        face,
                        ident.match.name if ident and ident.match else None,
                    )

        alerting = assessment.status in (Status.ALERT, Status.APPROACHING)
        with_frame = self.preview_wanted or alerting
        min_gap = 0.02 if with_frame else STATUS_ONLY_INTERVAL
        shot, state.pending_shot = state.pending_shot, None
        if (
            assessment.alert_started
            or assessment.continued
            or assessment.level_raised
            or shot is not None
            or now - state.last_emit >= min_gap
        ):
            state.last_emit = now
            self._emit_snapshot(
                Snapshot(
                    status=state.status_smoother.display(
                        Status.WATCHING
                        if assessment.status is Status.NOTICE and not settings.discreet
                        else assessment.status,
                        now,
                    ),
                    raw_status=assessment.status,
                    frame=_preview(frame) if with_frame else None,
                    tracks=display_tracks(
                        TrackView(
                            t.track_id,
                            (
                                t.box.x1 / width,
                                t.box.y1 / height,
                                t.box.x2 / width,
                                t.box.y2 / height,
                            ),
                            assessment.roles.get(t.track_id, Role.PENDING),
                            t.score,
                            _name(state.ids.get(t.track_id)),
                            # Camera-only preview frames reuse the latest analysis verdict.
                            seen=t.last_seen == state.last_detect,
                        )
                        for t in self._tracker.tracks
                        if now - t.last_seen < 0.35
                    ),
                    self_zone=self._alerts.policy.self_zone
                    if self._alerts.policy.exclude_self
                    else None,
                    intruders=assessment.intruders,
                    people=assessment.people,
                    alert_started=assessment.alert_started,
                    continued=assessment.continued,
                    alert_thumbnail=shot.image if shot else None,
                    alert_shot_id=shot.shot_id if shot else 0,
                    alert_face=shot.face if shot else None,
                    alert_name=shot.name if shot else None,
                    alert_level=assessment.alert_level,
                    level_raised=assessment.level_raised,
                    alert_ts=state.alert_ts,
                    evade_near=any(
                        t.last_seen == state.last_detect
                        and (
                            assessment.roles.get(t.track_id) is Role.APPROACHING
                            or (
                                assessment.roles.get(t.track_id) is Role.INTRUDER
                                and t.box.height / height
                                >= SENSITIVITY[settings.sensitivity][2]
                                * EVADE_NEAR_HEIGHT_MULTIPLIER
                            )
                        )
                        for t in self._tracker.tracks
                    ),
                    camera_fps=camera.fps,
                    analysis_fps=max(0, len(state.analysis_times) - 1) / 2.0,
                    latency_ms=state.latency_ms,
                )
            )
            # Report each alert start exactly once.
            assessment.alert_started = assessment.continued = False
            assessment.level_raised = False


def _recognise_tracks(
    faces: FaceEmbedder,
    people: PeopleSnapshot,
    frame: Image,
    tracks: list[Track],
    now: float,
    candidates: list[Track],
    state: _LoopState,
) -> None:
    budget = FACES_PER_STEP
    # Unrecognised and face-wanted tracks first, nearest (largest) first.
    order = sorted(
        candidates,
        key=lambda t: (
            t.track_id not in state.face_wanted,
            (state.ids.get(t.track_id) or _Identity()).match is not None,
            -t.box.height,
        ),
    )
    for track in order:
        if budget <= 0:
            break
        ident = state.ids.setdefault(track.track_id, _Identity())
        wait = RECHECK_KNOWN_SECONDS if ident.match is not None else RECHECK_UNKNOWN_SECONDS
        if now - ident.last_try < wait and track.track_id not in state.face_wanted:
            continue
        ident.last_try = now
        budget -= 1
        found = faces.embed(frame, track.box)
        if found is None:
            continue
        if found.box.width >= ident.face_size:
            ident.face, ident.face_size = found.embedding, found.box.width
        match = people.match(found.embedding)
        if match is not None:
            ident.match = match
        wanted = state.face_wanted.pop(track.track_id, None)
        if wanted is not None and state.pending_shot is None:
            image = alert_thumbnail(frame, [track.box])
            if image is not None:
                state.pending_shot = _Shot(wanted[0], image, ident.face, _name(ident))


class _LoopState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.motion = MotionMap()
        self.seq = 0
        self.last_detect = 0.0
        self.last_emit = 0.0
        self.analysis_times: list[float] = []
        self.assessment = Assessment(Status.CLEAR)
        self.status_smoother = StatusSmoother()
        self.latency_ms = 0.0
        self.camera_ok = False
        self.alert_ts: float | None = None
        self.last_thumb = float("-inf")
        self.pending_shot: _Shot | None = None
        self.shot_counter = 0
        self.ids: dict[int, _Identity] = {}
        # track id -> (history item to upgrade, deadline) once a face is visible
        self.face_wanted: dict[int, tuple[int, float]] = {}


@dataclass(frozen=True, slots=True)
class _Shot:
    shot_id: int
    image: NDArray[np.uint8]
    face: Vector | None
    name: str | None


def _name(ident: _Identity | None) -> str | None:
    return ident.match.name if ident is not None and ident.match is not None else None


def alert_thumbnail(frame: Image, boxes: list[Box]) -> NDArray[np.uint8] | None:
    """Crop around the alerting people, mirrored like the preview, at most 240x160."""

    if not boxes:
        return None
    height, width = frame.shape[:2]
    x1, y1 = min(b.x1 for b in boxes), min(b.y1 for b in boxes)
    x2, y2 = max(b.x2 for b in boxes), max(b.y2 for b in boxes)
    pad_x, pad_y = (x2 - x1) * _THUMB_PADDING, (y2 - y1) * _THUMB_PADDING
    left, top = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
    right, bottom = min(width, int(x2 + pad_x) + 1), min(height, int(y2 + pad_y) + 1)
    if right - left < 2 or bottom - top < 2:
        return None
    crop = frame[top:bottom, left:right]  # a view: no full-frame copy
    scale = min(THUMB_MAX_SIZE[0] / crop.shape[1], THUMB_MAX_SIZE[1] / crop.shape[0], 1.0)
    size = (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale)))
    small = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(cv2.cvtColor(cv2.flip(small, 1), cv2.COLOR_BGR2RGB), np.uint8)


def _candidates(
    tracks: list[Track], now: float, height: int, min_height: float, state: _LoopState
) -> list[Track]:
    previous = state.assessment.roles
    return [
        t
        for t in tracks
        if t.last_seen == now
        and previous.get(t.track_id) is not Role.SELF
        and t.box.height >= min_height * height
    ]


def _known_level(ident: _Identity | None) -> str | None:
    return ident.match.level.value if ident is not None and ident.match is not None else None


def _preview(frame: Image) -> NDArray[np.uint8]:
    height, width = frame.shape[:2]
    if width > _PREVIEW_MAX_WIDTH:
        scale = _PREVIEW_MAX_WIDTH / width
        frame = np.asarray(
            cv2.resize(
                frame, (_PREVIEW_MAX_WIDTH, round(height * scale)), interpolation=cv2.INTER_AREA
            ),
            dtype=np.uint8,
        )
    return np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8)


def _log_analysis(
    log: DiagnosticsLog,
    assessment: Assessment,
    detections: list[Detection],
    tracks: list[Track],
    now: float,
    width: int,
    height: int,
    state: _LoopState,
    camera_fps: float,
    moved: bool,
    self_zone: Zone | None,
) -> None:
    def norm(box: Box) -> list[float]:
        return [
            round(box.x1 / width, 3),
            round(box.y1 / height, 3),
            round(box.x2 / width, 3),
            round(box.y2 / height, 3),
        ]

    # Rate by what is in view: alerting people at full rate, other people at 2 Hz,
    # nobody but the user at 1 Hz. The first versions hit the daily size cap by
    # mid-afternoon on "high" sensitivity.
    roles = set(assessment.roles.values())
    if roles & {Role.INTRUDER, Role.APPROACHING}:
        level = "full"
    elif roles - {Role.SELF}:
        level = "people"
    else:
        level = "quiet"
    # A track's box is exactly the detection it matched this step; only log the
    # detections no track explains (dets_v=2 tells the replay tool).
    track_boxes = {t.box for t in tracks if t.last_seen == now}
    log.analysis(
        status=(Status.WATCHING if assessment.status is Status.NOTICE else assessment.status).value,
        notice=bool(assessment.notice_track_ids),
        notice_track_ids=list(assessment.notice_track_ids),
        alert_ts=state.alert_ts,
        level=level,
        alert_started=assessment.alert_started,
        continued=assessment.continued,
        mono=round(now, 3),
        frame=[width, height],
        camera_fps=round(camera_fps, 1),
        latency_ms=round(state.latency_ms, 1),
        scene_moved=moved,
        self_zone=list(self_zone) if self_zone else None,
        dets_v=2,
        dets=[[*norm(d.box), round(d.score, 3)] for d in detections if d.box not in track_boxes],
        tracks=[
            {
                "id": t.track_id,
                "box": norm(t.box),
                "score": round(t.score, 3),
                "role": assessment.roles.get(t.track_id, Role.PENDING).value,
                "hits": t.hits,
                "age": round(now - t.first_seen, 2),
                "seen": t.last_seen == now,
                "disp": round(t.displacement(1.0), 3),
                "growth": round(t.growth(1.0), 3),
                "motion": round(state.motion.fraction(t.box), 3),
                "known": _known_level(state.ids.get(t.track_id)),
            }
            for t in tracks
        ],
    )
