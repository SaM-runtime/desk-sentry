"""離座看守: who is photographed while the user is away."""

from desk_sentry.away_guard import (
    BETTER_SHOT_GROWTH,
    LINGER_SECONDS,
    MAX_VISITS_PER_EPISODE,
    AwayGuard,
)
from desk_sentry.detector import Box
from desk_sentry.sentry import Policy, Role, Status
from desk_sentry.tracker import Track, Tracker
from test_sentry import USER, ZONE, H, armed, det, walker

NEAR = 0.30  # medium sensitivity: people at least this tall count as near


def track(tid: int, box: Box, now: float) -> Track:
    return Track(tid, box, 0.8, first_seen=now, last_seen=now, hits=3)


def update(guard: AwayGuard, now: float, away: bool, tracks: list[Track], roles: dict):
    return guard.update(
        now, away=away, tracks=tracks, roles=roles, frame_height=H, near_height=NEAR
    )


def test_nobody_is_recorded_while_the_user_is_at_the_desk() -> None:
    guard = AwayGuard()
    near = track(1, Box(100, 100, 300, 600), 0.0)
    assert update(guard, 0.0, False, [near], {1: Role.INTRUDER}) == []
    assert not guard.watching


def test_near_visitor_is_photographed_and_again_only_when_clearly_closer() -> None:
    guard = AwayGuard()
    small = Box(100, 300, 200, 560)  # 260 px tall: above the near line
    first = update(guard, 0.0, True, [track(7, small, 0.0)], {7: Role.INTRUDER})
    assert len(first) == 1 and not first[0].seated
    visit_id = first[0].visit_id

    # Same size again: no new photo.
    assert update(guard, 2.0, True, [track(7, small, 2.0)], {7: Role.INTRUDER}) == []
    # Clearly closer (area grew past the threshold): the photo is replaced.
    scale = BETTER_SHOT_GROWTH**0.5 * 1.05
    bigger = Box(100, 300, 100 + 100 * scale, 300 + 260 * scale)
    again = update(guard, 3.0, True, [track(7, bigger, 3.0)], {7: Role.INTRUDER})
    assert [r.visit_id for r in again] == [visit_id]


def test_far_passers_by_and_pending_people_are_not_visitors() -> None:
    guard = AwayGuard()
    far = track(1, Box(100, 100, 140, 180), 0.0)  # 80 px: far behind
    pending = track(2, Box(300, 100, 500, 600), 0.0)
    roles = {1: Role.INTRUDER, 2: Role.PENDING}
    assert update(guard, 0.0, True, [far, pending], roles) == []


def test_someone_sitting_in_the_seat_is_recorded_as_seated() -> None:
    guard = AwayGuard()
    update(guard, 0.0, True, [], {})
    seated = update(guard, 1.0, False, [track(3, USER, 1.0)], {3: Role.SELF})
    assert len(seated) == 1 and seated[0].seated


def test_the_user_present_when_the_episode_starts_is_not_a_visitor() -> None:
    guard = AwayGuard()
    me = track(1, Box(900, 100, 1100, 700), 0.0)
    assert update(guard, 0.0, True, [me], {1: Role.SELF}) == []
    assert update(guard, 1.0, True, [track(1, me.box, 1.0)], {1: Role.SELF}) == []


def test_recording_stops_after_the_linger_period() -> None:
    guard = AwayGuard()
    update(guard, 0.0, True, [], {})
    update(guard, 1.0, False, [], {})
    assert guard.watching
    late = track(4, Box(100, 100, 300, 600), 1.0 + LINGER_SECONDS)
    assert update(guard, 1.0 + LINGER_SECONDS, False, [late], {4: Role.INTRUDER}) == []
    assert not guard.watching


def test_visit_ids_stay_unique_across_episodes_and_are_capped() -> None:
    guard = AwayGuard()
    ids = []
    for tid in range(MAX_VISITS_PER_EPISODE + 5):
        box = Box(10, 100, 210, 600)
        ids += [
            r.visit_id
            for r in update(guard, 0.0, True, [track(tid, box, 0.0)], {tid: Role.INTRUDER})
        ]
    assert len(ids) == MAX_VISITS_PER_EPISODE
    guard.reset()
    again = update(guard, 5.0, True, [track(99, Box(10, 100, 210, 600), 5.0)], {99: Role.INTRUDER})
    assert again[0].visit_id not in ids


def test_with_the_real_sentry_a_visitor_and_the_person_who_sits_down_are_recorded() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker(ttl_seconds=0.3)
    guard = AwayGuard()
    requests = []

    def feed(frames):
        for now, dets in frames:
            tracks = tracker.update(dets, now)
            result = engine.assess(tracks, now, (1280, H))
            requests.extend(update(guard, now, result.status is Status.AWAY, tracks, result.roles))
            yield result

    list(feed([(i * 0.1, [det(USER)]) for i in range(10)]))
    away = list(feed([(1.0 + i * 0.5, []) for i in range(40)]))
    assert away[-1].status is Status.AWAY and requests == []

    t0 = 21.5
    visitor = list(feed([(t0 + i * 0.1, [det(walker(i * 0.1))]) for i in range(5)]))
    assert all(r.intruders == 0 for r in visitor)  # no alert: nobody to warn
    assert len(requests) == 1 and not requests[0].seated

    back = list(feed([(t0 + 1 + i * 0.1, [det(USER)]) for i in range(5)]))
    assert back[-1].status is Status.CLEAR
    assert requests[-1].seated  # whoever sat down is on the record
