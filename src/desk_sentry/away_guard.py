"""離座看守: who came to the desk while the user was away.

Pure logic: no camera, no Qt. While the sentry reports the user away, every
person close enough to matter (anyone who would have raised an alert, plus
anyone who sits down in the user's seat) becomes one *visit*. Each visit asks
for a photo when it starts and again whenever the person comes clearly closer,
so the kept photo is the nearest, most recognisable one.

Recording continues for ``LINGER_SECONDS`` after the user "returns": whoever
sat down in the seat is exactly who the user wants to see, and the sentry
cannot tell the owner from someone else sitting in the same chair.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from desk_sentry.detector import Box
from desk_sentry.sentry import Role
from desk_sentry.tracker import Track

LINGER_SECONDS = 20.0
MIN_SHOT_GAP_SECONDS = 1.0
# A new photo replaces the kept one only when the person looks this much larger.
BETTER_SHOT_GROWTH = 1.15
MAX_VISITS_PER_EPISODE = 20

_RECORDED_ROLES = frozenset({Role.INTRUDER, Role.APPROACHING, Role.SELF, Role.KNOWN, Role.STILL})


@dataclass(frozen=True, slots=True)
class VisitRequest:
    """Take (or replace) the photo of ``visit_id`` from ``box`` in this frame."""

    visit_id: int
    box: Box
    seated: bool  # the person is in the user's seat


@dataclass(slots=True)
class _Visit:
    visit_id: int
    best_area: float
    last_shot: float
    seated: bool


@dataclass(slots=True)
class AwayGuard:
    """Turns raw sentry verdicts into photo requests for people seen while away."""

    _next_id: int = 0
    _watching: bool = False
    _linger_until: float | None = None
    _visits: dict[int, _Visit] = field(default_factory=dict)
    _ignored: set[int] = field(default_factory=set)

    @property
    def watching(self) -> bool:
        return self._watching

    def reset(self) -> None:
        """Forget the current episode; visit ids keep increasing."""

        self._watching = False
        self._linger_until = None
        self._visits.clear()
        self._ignored.clear()

    def update(
        self,
        now: float,
        *,
        away: bool,
        tracks: Iterable[Track],
        roles: Mapping[int, Role],
        frame_height: int,
        near_height: float,
    ) -> list[VisitRequest]:
        tracks = list(tracks)
        if away:
            if not self._watching:
                self._watching = True
                self._visits.clear()
                # Whoever is still the user when the episode starts (standing
                # beside the desk, say) is not a visitor.
                self._ignored = {tid for tid, role in roles.items() if role is Role.SELF}
            self._linger_until = None
        elif self._watching:
            if self._linger_until is None:
                self._linger_until = now + LINGER_SECONDS
            if now >= self._linger_until:
                self.reset()
                return []
        else:
            return []

        requests: list[VisitRequest] = []
        for track in tracks:
            tid = track.track_id
            if track.last_seen != now or tid in self._ignored:
                continue
            role = roles.get(tid)
            if role not in _RECORDED_ROLES:
                continue
            seated = role is Role.SELF
            near = track.box.height >= near_height * frame_height
            # Far passers-by and a coat on the far wall are not visitors; anyone
            # near the desk is, even when standing still.
            if not seated and not near:
                continue
            area = track.box.area
            visit = self._visits.get(tid)
            if visit is None:
                if len(self._visits) >= MAX_VISITS_PER_EPISODE:
                    continue
                self._next_id += 1
                visit = _Visit(self._next_id, area, now, seated)
                self._visits[tid] = visit
                requests.append(VisitRequest(visit.visit_id, track.box, seated))
                continue
            sat_down = seated and not visit.seated
            closer = area >= visit.best_area * BETTER_SHOT_GROWTH
            if (sat_down or closer) and now - visit.last_shot >= MIN_SHOT_GAP_SECONDS:
                visit.best_area = max(visit.best_area, area)
                visit.last_shot = now
                visit.seated = visit.seated or seated
                requests.append(VisitRequest(visit.visit_id, track.box, visit.seated))
        return requests
