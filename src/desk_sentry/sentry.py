"""Decides who is "behind you" and when to raise or clear the rear-view alert.

Pure logic: no camera, no Qt. Every rule here is covered by tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from desk_sentry.config import SENSITIVITY
from desk_sentry.detector import Box
from desk_sentry.people import Level
from desk_sentry.tracker import Track

Zone = tuple[float, float, float, float]

CONFIRM_HITS = 2
ALERT_HOLD_SECONDS = 1.5
# Measured: 57 % of alerts restarted within 30 s of the previous one ending (the
# same colleague moving around), so the flash and popup kept re-firing. An alert
# that resumes this soon is reported as a continuation, not a new alert.
REALERT_QUIET_SECONDS = 20.0
APPROACH_GROWTH = 1.2
_MOVE_DISPLACEMENT = 0.2
_MOVE_GROWTH = 0.15
_SELF_MIN_HEIGHT = 0.35
_SELF_MIN_AREA = 0.08
_SELF_CALIBRATION_SECONDS = 1.5
_SELF_MIN_INSIDE = 0.6
_SELF_MIN_AREA_RATIO = 0.35
# Whatever is already in frame right after arming is the baseline scene: it has
# to move before it can raise an alert.
WARMUP_SECONDS = 2.0
# Where something was muted as "still" (a coat, a poster, a seated colleague);
# a flickering detection reappearing there does not re-alert until it moves.
STATIC_MEMORY_SECONDS = 600.0
_STATIC_MATCH_IOU = 0.5
# A person who really walks in changes pixels inside their own box. A box that
# just flickers into existence over an unchanged scene (clothes rack, coat,
# poster whose score hovers around the threshold) is treated as static.
ENTRY_MOTION_FRACTION = 0.04
ENTRY_WINDOW_SECONDS = 0.6
NOTICE_SIDE_EDGE = 0.15
NOTICE_INWARD_DISPLACEMENT = 0.01
# Growth is a ratio: halve the excess above 1.0, not the entire ratio.
NOTICE_APPROACH_GROWTH = 1.0 + (APPROACH_GROWTH - 1.0) / 2.0

# Measured on a real office day: most alerts were colleagues crossing an aisle
# several metres behind (10-26 % of frame height). People below the
# sensitivity's "near" height are shown as passing and never alert: box growth
# is too noisy at that size to tell approach from swinging legs, occlusion or a
# colleague standing up. Someone really coming over crosses the near height
# (~5 m) a few seconds before reaching the desk and alerts then.
#
# Someone walking towards the camera grows in width *and* height, keeps growing,
# and moves little sideways. Legs swinging, partial occlusion and a colleague
# standing up (height only) all fail at least one of these.
_APPROACH_WIDTH_GROWTH = 1.1
_APPROACH_RECENT_GROWTH = 1.04
_APPROACH_MAX_SIDEWAYS = 1.2
# Nobody to warn when the user has left the desk. Also removes the alerts the
# user used to trigger themselves when leaving or arriving.
AWAY_SECONDS = 15.0
# The user standing up or leaving often makes the detector drop their track and
# start a new one where they were; that new box is still the user.
_HANDOFF_SECONDS = 2.0
_HANDOFF_MIN_HEIGHT = 0.45
_HANDOFF_MIN_IOU = 0.2
_DUPLICATE_MIN_HEIGHT = 0.6
_DUPLICATE_MIN_IOU = 0.3
_DUPLICATE_SELF_MOTION = 0.35
# An empty chair in the seat zone can be detected all night with a low score and
# zero motion, and a passer-by once taken for the user must not keep them
# "present": only a confident detection sitting in the seat zone counts.
_PRESENT_SCORE = 0.6  # measured: user p5 0.71, empty chair max 0.55
_PRESENT_ZONE_IOU = 0.3

# A low, smaller box beside the seated user can be their mouse hand/arm.
# This is a per-frame role only: never retain its identity or presence timestamp.
SELF_LIMB_TOP = 0.10
SELF_LIMB_HORIZONTAL = 0.08
SELF_LIMB_HEIGHT_RATIO = 0.9

# While labelled people with a lower alert level exist, a newcomer gets this
# long to be recognised before an unrecognised person is alerted on.
IDENTIFY_GRACE_SECONDS = 0.6

MotionProbe = Callable[[Box], float]


class Role(StrEnum):
    PENDING = "pending"
    SELF = "self"
    FAR = "far"
    PASSING = "passing"
    KNOWN = "known"  # recognised person whose alert level is "low"
    STILL = "still"
    INTRUDER = "intruder"
    APPROACHING = "approaching"


class Status(StrEnum):
    PAUSED = "paused"
    NO_CAMERA = "no-camera"
    ERROR = "error"
    CLEAR = "clear"
    AWAY = "away"
    WATCHING = "watching"
    NOTICE = "notice"
    ALERT = "alert"
    APPROACHING = "approaching"


@dataclass(slots=True)
class Assessment:
    status: Status
    intruders: int = 0
    people: int = 0
    alert_started: bool = False
    # Alert resumed within REALERT_QUIET_SECONDS of the previous one: show it, but
    # no new flash / sound / action.
    continued: bool = False
    calibrated_zone: Zone | None = None
    roles: dict[int, Role] = field(default_factory=dict)
    # Strongest alert level among the people alerting now (None: no alert).
    alert_level: Level | None = None
    # A higher-level person joined an alert that was running at a lower level.
    level_raised: bool = False
    notice_track_ids: tuple[int, ...] = ()


@dataclass(slots=True)
class Policy:
    sensitivity: str = "medium"
    exclude_self: bool = True
    self_zone: Zone | None = None
    stationary_mute_seconds: float = 10.0
    alert_far_passersby: bool = False
    unknown_level: Level = Level.HIGH  # alert level for people nobody labelled


class AlertEngine:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy()
        self._last_moved: dict[int, float] = {}
        self._unproven: dict[int, float] = {}  # track id -> entry evidence deadline
        self._self_ids: set[int] = set()
        self._alert_active = False
        self._last_intruder_at: float | None = None
        self._last_alert_end: float | None = None
        self._episode_level: Level | None = None
        self._armed_at: float | None = None
        self._static: list[tuple[Box, float]] = []  # normalized box, expiry
        self._last_self_seen: float | None = None
        self._last_self_box: Box | None = None

    def reset(self) -> None:
        self._last_moved.clear()
        self._unproven.clear()
        self._self_ids.clear()
        self._alert_active = False
        self._last_intruder_at = None
        self._last_alert_end = None
        self._episode_level = None
        self._armed_at = None
        self._last_self_seen = None
        self._last_self_box = None

    @property
    def alert_active(self) -> bool:
        return self._alert_active

    def assess(
        self,
        tracks: list[Track],
        now: float,
        frame_size: tuple[int, int],
        motion: MotionProbe | None = None,
        identities: Mapping[int, Level] | None = None,
        awaiting: set[int] | None = None,
    ) -> Assessment:
        """Classify every track and update the alert state.

        ``motion(box)``: fraction of pixels that changed recently inside ``box``.
        ``identities``: alert level of tracks recognised as a labelled person.
        ``awaiting``: tracks still being recognised (see IDENTIFY_GRACE_SECONDS).
        """
        identities = identities or {}
        awaiting = awaiting or set()
        width, height = frame_size
        policy = self.policy
        if self._armed_at is None:
            self._armed_at = now
        self._static = [(b, exp) for b, exp in self._static if exp > now]
        min_score, min_height, near_height = SENSITIVITY.get(
            policy.sensitivity, SENSITIVITY["medium"]
        )
        calibrated: Zone | None = None
        if policy.exclude_self and policy.self_zone is None:
            calibrated = _auto_calibrate(tracks, now, width, height)
            if calibrated is not None:
                policy.self_zone = calibrated
        zone_box = (
            Box(
                *(
                    v * s
                    for v, s in zip(policy.self_zone, (width, height, width, height), strict=True)
                )
            )
            if policy.exclude_self and policy.self_zone is not None
            else None
        )

        live_ids = {t.track_id for t in tracks}
        self._last_moved = {k: v for k, v in self._last_moved.items() if k in live_ids}
        self._unproven = {k: v for k, v in self._unproven.items() if k in live_ids}
        self._self_ids &= live_ids
        current_self = [t for t in tracks if t.track_id in self._self_ids]
        tentative_self = (
            _largest_seated(tracks, width, height)
            if policy.exclude_self and zone_box is None
            else None
        )
        limb_reference = max(
            (
                t
                for t in tracks
                if t.last_seen == now
                and (
                    t is tentative_self
                    or (zone_box is not None and _is_self(t, zone_box, self._self_ids))
                    or (
                        zone_box is not None
                        and self._is_self_continuation(t, current_self, now, height, motion)
                    )
                )
            ),
            key=lambda t: t.box.area,
            default=None,
        )
        roles: dict[int, Role] = {}
        intruders = 0
        approaching = False
        alert_levels: list[Level] = []
        for track in tracks:
            tid = track.track_id
            if tid not in self._last_moved:
                if self._is_baseline(track, now, width, height):
                    self._last_moved[tid] = float("-inf")
                    self._remember_static(track.box, now, width, height)
                elif motion is None or policy.stationary_mute_seconds <= 0:
                    self._last_moved[tid] = track.first_seen
                else:
                    self._last_moved[tid] = float("-inf")
                    self._unproven[tid] = now + ENTRY_WINDOW_SECONDS
            if tid in self._unproven:
                assert motion is not None
                if motion(track.box) >= ENTRY_MOTION_FRACTION:
                    del self._unproven[tid]
                    self._last_moved[tid] = now
                elif now >= self._unproven[tid]:
                    del self._unproven[tid]  # never showed motion: static thing
                    self._remember_static(track.box, now, width, height)
            moved_at = self._last_moved[tid]
            if (
                track.displacement(1.0) >= _MOVE_DISPLACEMENT
                or abs(track.growth(1.0) - 1.0) >= _MOVE_GROWTH
            ):
                moved_at = self._last_moved[tid] = now

            if (
                limb_reference is not None
                and track is not limb_reference
                and _is_self_limb(track.box, limb_reference.box, width, height)
            ):
                role = Role.SELF
            elif (
                track is tentative_self
                or (zone_box is not None and _is_self(track, zone_box, self._self_ids))
                or (
                    zone_box is not None
                    and self._is_self_continuation(track, current_self, now, height, motion)
                )
            ):
                role = Role.SELF
                self._self_ids.add(tid)
                # People walking behind an empty chair make its box "move", so
                # only a confident detection counts as the user being there.
                if (
                    track.score >= _PRESENT_SCORE
                    and zone_box is not None
                    and track.box.iou(zone_box) >= _PRESENT_ZONE_IOU
                ):
                    self._last_self_seen = now
                    self._last_self_box = track.box
            elif (
                track.hits < CONFIRM_HITS
                or track.mean_score < min_score  # one lucky frame is not enough
                or tid in self._unproven
            ):
                role = Role.PENDING
            elif track.box.height < min_height * height:
                role = Role.FAR
            elif (
                policy.stationary_mute_seconds > 0
                and now - moved_at >= policy.stationary_mute_seconds
            ):
                role = Role.STILL
                self._remember_static(track.box, now, width, height)
            elif identities.get(tid) is Level.LOW:
                role = Role.KNOWN
            elif tid in awaiting and now - track.first_seen < IDENTIFY_GRACE_SECONDS:
                role = Role.PENDING  # give recognition a moment before alerting
            elif not policy.alert_far_passersby and track.box.height < near_height * height:
                role = Role.PASSING
            elif is_approaching(track):
                role = Role.APPROACHING
            else:
                role = Role.INTRUDER
            if role in (Role.INTRUDER, Role.APPROACHING):
                intruders += 1
                approaching = approaching or role is Role.APPROACHING
                level = identities.get(tid, policy.unknown_level)
                alert_levels.append(Level.MEDIUM if level is Level.LOW else level)
            roles[tid] = role

        if self._last_self_seen is None:
            self._last_self_seen = now  # grace period right after arming
        away = zone_box is not None and now - self._last_self_seen >= AWAY_SECONDS
        if away:
            intruders, approaching = 0, False
            if self._alert_active:
                self._alert_active = False
                self._last_alert_end = now

        started = continued = raised = False
        fresh_intruder = any(
            roles[track.track_id] in (Role.INTRUDER, Role.APPROACHING) and track.last_seen == now
            for track in tracks
        )
        current_level = (
            Level.HIGH if Level.HIGH in alert_levels else Level.MEDIUM if alert_levels else None
        )
        if intruders:
            if self._alert_active:
                self._last_intruder_at = now
            elif fresh_intruder:
                self._last_intruder_at = now
                self._alert_active = True
                if (
                    self._last_alert_end is not None
                    and now - self._last_alert_end < REALERT_QUIET_SECONDS
                ):
                    continued = True
                else:
                    started = True
                    self._episode_level = None
            if self._alert_active:
                if current_level is Level.HIGH and self._episode_level is Level.MEDIUM:
                    raised = not started
                if self._episode_level is not Level.HIGH:
                    self._episode_level = current_level
        elif (
            self._alert_active
            and self._last_intruder_at is not None
            and now - self._last_intruder_at >= ALERT_HOLD_SECONDS
        ):
            self._alert_active = False
            self._last_alert_end = now

        episode_level = self._episode_level if self._alert_active else None
        if away:
            status = Status.AWAY
        elif approaching and self._alert_active:
            status = Status.APPROACHING
        elif self._alert_active:
            status = Status.ALERT
        elif any(
            r in (Role.STILL, Role.PENDING, Role.FAR, Role.PASSING, Role.KNOWN)
            for r in roles.values()
        ):
            status = Status.WATCHING
        else:
            status = Status.CLEAR
        people = sum(1 for r in roles.values() if r is not Role.SELF)
        notice_ids: tuple[int, ...] = ()
        if status is Status.WATCHING:
            notice_ids = self._notice_tracks(tracks, roles, identities, now, width)
            if notice_ids:
                status = Status.NOTICE
        return Assessment(
            status,
            intruders,
            people,
            started,
            continued,
            calibrated,
            roles,
            alert_level=episode_level,
            level_raised=raised,
            notice_track_ids=notice_ids,
        )

    def _notice_tracks(
        self,
        tracks: list[Track],
        roles: Mapping[int, Role],
        identities: Mapping[int, Level],
        now: float,
        width: int,
    ) -> tuple[int, ...]:
        minimum_score = SENSITIVITY.get(self.policy.sensitivity, SENSITIVITY["medium"])[0]
        zone = self.policy.self_zone
        seat_x = (zone[0] + zone[2]) / 2.0 if zone is not None else 0.5
        noticed = []
        for track in tracks:
            # PASSING/STILL/KNOWN remain excluded as required; roles are never rewritten.
            if (
                roles[track.track_id] not in (Role.FAR, Role.PENDING)
                or track.hits < CONFIRM_HITS
                or track.mean_score < minimum_score
                or track.last_seen != now
                or identities.get(track.track_id) is Level.LOW
            ):
                continue
            sideways = False
            if track.history and now - track.first_seen <= ENTRY_WINDOW_SECONDS:
                first_x = track.history[0][1].center[0] / width
                current_x = track.box.center[0] / width
                sideways = (first_x < NOTICE_SIDE_EDGE or first_x > 1.0 - NOTICE_SIDE_EDGE) and (
                    (current_x - first_x) * (seat_x - first_x) > 0
                    and abs(current_x - first_x) >= NOTICE_INWARD_DISPLACEMENT
                    and abs(current_x - seat_x) < abs(first_x - seat_x)
                )
            approaching = (
                track.growth(1.0) >= NOTICE_APPROACH_GROWTH
                and track.width_growth(1.0) >= _APPROACH_WIDTH_GROWTH
                and track.growth(0.5) >= _APPROACH_RECENT_GROWTH
                and track.displacement(1.0) <= _APPROACH_MAX_SIDEWAYS
            )
            if sideways or approaching:
                noticed.append(track.track_id)
        return tuple(noticed)

    def _is_self_continuation(
        self,
        track: Track,
        current_self: list[Track],
        now: float,
        height: int,
        motion: MotionProbe | None,
    ) -> bool:
        """A fresh box that is really the user standing up, leaning out or leaving."""

        if now - track.first_seen > 1.0:
            return False
        box = track.box
        last = self._last_self_box
        if (
            not current_self
            and last is not None
            and self._last_self_seen is not None
            and now - self._last_self_seen <= _HANDOFF_SECONDS
            and box.height >= _HANDOFF_MIN_HEIGHT * height
            and box.iou(last) >= _HANDOFF_MIN_IOU
        ):
            return True
        # The user moving a lot can yield a second, overlapping full-height box.
        return motion is not None and any(
            box.height >= _DUPLICATE_MIN_HEIGHT * height
            and box.iou(me.box) >= _DUPLICATE_MIN_IOU
            and motion(me.box) >= _DUPLICATE_SELF_MOTION
            for me in current_self
        )

    def _is_baseline(self, track: Track, now: float, width: int, height: int) -> bool:
        if self.policy.stationary_mute_seconds <= 0 or self._armed_at is None:
            return False
        if now - self._armed_at < WARMUP_SECONDS:
            return True
        box = track.box.scaled(1 / width, 1 / height)
        return any(box.iou(known) >= _STATIC_MATCH_IOU for known, _ in self._static)

    def _remember_static(self, box: Box, now: float, width: int, height: int) -> None:
        norm = box.scaled(1 / width, 1 / height)
        expiry = now + STATIC_MEMORY_SECONDS
        self._static = [(b, e) for b, e in self._static if b.iou(norm) < _STATIC_MATCH_IOU]
        self._static.append((norm, expiry))


def _is_self_limb(box: Box, user: Box, width: int, height: int) -> bool:
    return (
        box.y2 >= 0.85 * height
        and box.x1 <= user.x2 + SELF_LIMB_HORIZONTAL * width
        and box.x2 >= user.x1 - SELF_LIMB_HORIZONTAL * width
        and box.y1 >= user.y1 + SELF_LIMB_TOP * height
        and box.height <= user.height * SELF_LIMB_HEIGHT_RATIO
    )


def is_approaching(track: Track) -> bool:
    return (
        track.growth(1.0) >= APPROACH_GROWTH
        and track.width_growth(1.0) >= _APPROACH_WIDTH_GROWTH
        and track.growth(0.5) >= _APPROACH_RECENT_GROWTH
        and track.displacement(1.0) <= _APPROACH_MAX_SIDEWAYS
    )


def _is_self(track: Track, zone: Box, self_ids: set[int]) -> bool:
    box = track.box
    if box.area <= 0.0 or zone.area <= 0.0:
        return False
    if track.track_id in self_ids:
        # Once a track is the user it stays the user: leaning, standing up and
        # walking away from the desk must never look like someone else.
        return True
    iw = min(box.x2, zone.x2) - max(box.x1, zone.x1)
    ih = min(box.y2, zone.y2) - max(box.y1, zone.y1)
    if iw <= 0.0 or ih <= 0.0:
        return False
    inside = iw * ih / box.area
    # A person walking behind you is smaller than you, or sticks out of your seat zone.
    return inside >= _SELF_MIN_INSIDE and box.area / zone.area >= _SELF_MIN_AREA_RATIO


def _largest_seated(tracks: list[Track], width: int, height: int) -> Track | None:
    """Most likely "you": large, reaching the bottom edge, near the horizontal centre."""

    candidates = [
        t
        for t in tracks
        if t.box.height >= _SELF_MIN_HEIGHT * height
        and t.box.area >= _SELF_MIN_AREA * width * height
        and t.box.y2 >= 0.85 * height
    ]
    if not candidates:
        return None

    def score(t: Track) -> float:
        off_centre = abs(t.box.center[0] / width - 0.5) * 2.0  # 0 centre .. 1 edge
        return t.box.area / (width * height) * (1.0 - 0.8 * off_centre)

    return max(candidates, key=score)


def _auto_calibrate(tracks: list[Track], now: float, width: int, height: int) -> Zone | None:
    """Treat the largest, steady, near-camera person as the user sitting at the desk."""

    track = _largest_seated(tracks, width, height)
    if (
        track is None
        or track.hits < CONFIRM_HITS
        or now - track.first_seen < _SELF_CALIBRATION_SECONDS
    ):
        return None
    return expand_zone(track.box, width, height)


def zone_for_point(boxes: list[Box], x: float, y: float) -> Zone | None:
    """Self zone from the person box the user clicked (normalized coordinates)."""

    hits = [b for b in boxes if b.x1 <= x <= b.x2 and b.y1 <= y <= b.y2]
    if not hits:
        return None
    return expand_zone(min(hits, key=lambda b: b.area), 1, 1)


def expand_zone(box: Box, width: int, height: int, margin: float = 0.15) -> Zone:
    dx, dy = box.width * margin, box.height * margin
    return (
        max(0.0, (box.x1 - dx) / width),
        max(0.0, (box.y1 - dy) / height),
        min(1.0, (box.x2 + dx) / width),
        min(1.0, (box.y2 + dy) / height),
    )
