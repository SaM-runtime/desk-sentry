"""Small greedy multi-person tracker with motion and approach statistics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from desk_sentry.detector import Box, Detection

_HISTORY_SECONDS = 3.0
# Detector boxes jitter by ~10 % frame to frame; motion statistics compare
# boxes averaged over this span instead of single noisy samples.
SMOOTHING_SECONDS = 0.3
# A walking track lost for a frame may be resumed by a new box this many box
# heights from its predicted position. Only tracks that really walked (smoothed
# displacement over _RESUME_WINDOW, so detector jitter does not count) qualify:
# a seated user whose box drops out must not hand their identity, and
# self-exclusion, to a passer-by.
_RESUME_DISTANCE = 1.5
_RESUME_MAX_GAP = 0.3  # a few frames: longer gaps are a different person
_RESUME_WINDOW = 0.5
_RESUME_MIN_DISPLACEMENT = 0.5
# Two boxes belong to one person on screen (e.g. upper body inside full body).
OVERLAP_IOU_THRESHOLD = 0.3
OVERLAP_SMALLER_THRESHOLD = 0.6


def boxes_overlap(a: Box, b: Box) -> bool:
    """Shared duplicate-box rule, including an upper body inside a full body."""

    smaller = min(a.area, b.area)
    if smaller <= 0.0:
        return False
    intersection = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1)) * max(
        0.0, min(a.y2, b.y2) - max(a.y1, b.y1)
    )
    return a.iou(b) >= OVERLAP_IOU_THRESHOLD or intersection / smaller >= OVERLAP_SMALLER_THRESHOLD


@dataclass(slots=True)
class Track:
    track_id: int
    box: Box
    score: float
    first_seen: float
    last_seen: float
    hits: int = 1
    history: deque[tuple[float, Box]] = field(default_factory=deque)
    scores: deque[float] = field(default_factory=lambda: deque(maxlen=5))

    def __post_init__(self) -> None:
        if not self.scores:
            self.scores.append(self.score)

    @property
    def mean_score(self) -> float:
        """Mean of the last few detection scores: one lucky frame is not enough."""

        return sum(self.scores) / len(self.scores)

    def record(self, now: float, box: Box, score: float) -> None:
        self.box = box
        self.score = score
        self.scores.append(score)
        self.last_seen = now
        self.hits += 1
        self.history.append((now, box))
        while self.history and now - self.history[0][0] > _HISTORY_SECONDS:
            self.history.popleft()

    def predict(self, now: float) -> Box:
        """Latest box moved by the recent velocity (constant-velocity guess)."""

        if len(self.history) < 2:
            return self.box
        (t0, a), (t1, b) = self.history[-2], self.history[-1]
        if t1 <= t0:
            return self.box
        dt = min(now - t1, 0.5)
        (ax, ay), (bx, by) = a.center, b.center
        vx, vy = (bx - ax) / (t1 - t0), (by - ay) / (t1 - t0)
        return Box(b.x1 + vx * dt, b.y1 + vy * dt, b.x2 + vx * dt, b.y2 + vy * dt)

    def _mean_box(self, end: float) -> Box | None:
        samples = [b for t, b in self.history if end - SMOOTHING_SECONDS <= t <= end]
        if not samples:
            return None
        n = len(samples)
        return Box(
            sum(b.x1 for b in samples) / n,
            sum(b.y1 for b in samples) / n,
            sum(b.x2 for b in samples) / n,
            sum(b.y2 for b in samples) / n,
        )

    def _smoothed_pair(self, window: float) -> tuple[Box, Box] | None:
        """(box ``window`` seconds before the latest sample, latest box), both smoothed."""

        if not self.history or self.last_seen - self.history[0][0] < window:
            return None
        old = self._mean_box(self.last_seen - window)
        new = self._mean_box(self.last_seen)
        if old is None or new is None or old.height <= 0.0 or new.height <= 0.0:
            return None
        return old, new

    def displacement(self, window: float) -> float:
        """Smoothed centre movement over ``window`` seconds, in box heights."""

        pair = self._smoothed_pair(window)
        if pair is None:
            return 0.0
        old, new = pair
        (ax, ay), (bx, by) = old.center, new.center
        return float(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5) / max(old.height, new.height)

    def width_growth(self, window: float) -> float:
        """Smoothed width ratio now / ``window`` seconds ago."""

        pair = self._smoothed_pair(window)
        if pair is None or pair[0].width <= 0.0:
            return 1.0
        old, new = pair
        return new.width / old.width

    def growth(self, window: float) -> float:
        """Smoothed height ratio now / ``window`` seconds ago; > 1 means coming closer."""

        pair = self._smoothed_pair(window)
        if pair is None:
            return 1.0
        old, new = pair
        return new.height / old.height


class Tracker:
    """Associates detections frame-to-frame by IoU, then by nearby centre."""

    def __init__(self, *, ttl_seconds: float = 1.5) -> None:
        self._ttl = ttl_seconds
        self._next_id = 1
        self.tracks: list[Track] = []

    def reset(self) -> None:
        self.tracks.clear()

    def update(self, detections: list[Detection], now: float) -> list[Track]:
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self._ttl]
        unmatched = list(detections)
        pairs: list[tuple[float, Track, Detection]] = []
        for track in self.tracks:
            predicted = track.predict(now)
            for det in unmatched:
                score = max(_match_score(track.box, det.box), _match_score(predicted, det.box))
                if score > 0.0:
                    pairs.append((score, track, det))
        pairs.sort(key=lambda p: p[0], reverse=True)

        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for _, track, det in pairs:
            if track.track_id in used_tracks or id(det) in used_dets:
                continue
            used_tracks.add(track.track_id)
            used_dets.add(id(det))
            track.record(now, det.box, det.score)

        for det in unmatched:
            if id(det) not in used_dets:
                track = Track(self._next_id, det.box, det.score, now, now)
                track.history.append((now, det.box))
                self._next_id += 1
                self.tracks.append(track)

        self._merge_successors(now)
        return self.tracks

    def _merge_successors(self, now: float) -> None:
        """Fold a track born this frame back into a recently lost one.

        A fast walker can outrun association for a frame; without this the old
        track lingers as a ghost for the whole TTL next to a fresh "pending" one,
        and the person shows two status cards.
        """

        pairs: list[tuple[float, Track, Track]] = []
        for lost in self.tracks:
            if lost.last_seen >= now:
                continue
            predicted = lost.predict(now)
            walked = (
                now - lost.last_seen <= _RESUME_MAX_GAP
                and lost.displacement(_RESUME_WINDOW) >= _RESUME_MIN_DISPLACEMENT
            )
            reach = _RESUME_DISTANCE if walked else 0.0
            for new in self.tracks:
                if new.first_seen == now:  # born this frame: no identity of its own yet
                    score = _match_score(predicted, new.box, max_dist=reach)
                    if score > 0.0:
                        pairs.append((score, lost, new))
        pairs.sort(key=lambda p: p[0], reverse=True)

        merged: set[int] = set()
        for _, lost, new in pairs:
            if lost.track_id in merged or new.track_id in merged:
                continue
            merged.update((lost.track_id, new.track_id))
            lost.history.extend(new.history)
            lost.scores.extend(new.scores)
            lost.box, lost.score = new.box, new.score
            lost.last_seen = new.last_seen
            lost.hits += new.hits
            self.tracks.remove(new)


def _match_score(a: Box, b: Box, *, max_dist: float = 0.8) -> float:
    iou = a.iou(b)
    if iou >= 0.2:
        return 1.0 + iou
    # Fast walkers at ~10 Hz analysis can move beyond IoU overlap; accept a
    # similarly sized box whose centre moved less than ~0.8 box heights.
    if a.height <= 0.0 or b.height <= 0.0:
        return 0.0
    ratio = b.height / a.height
    if not 0.6 <= ratio <= 1.67:
        return 0.0
    (ax, ay), (bx, by) = a.center, b.center
    dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / max(a.height, b.height)
    return max_dist - dist if dist < max_dist else 0.0
