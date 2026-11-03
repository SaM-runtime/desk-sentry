from collections import deque

from desk_sentry.detector import Box
from desk_sentry.sentry import ALERT_HOLD_SECONDS, AlertEngine, Policy, Role, Status
from desk_sentry.tracker import Track

FRAME_SIZE = (1280, 720)


def alert_engine() -> AlertEngine:
    engine = AlertEngine(Policy(exclude_self=False, stationary_mute_seconds=0))
    engine.assess([], -3.0, FRAME_SIZE)
    return engine


def intruder(*, last_seen: float, track_id: int = 1, hits: int = 2) -> Track:
    return Track(
        track_id=track_id,
        box=Box(100, 100, 300, 500),
        score=0.9,
        first_seen=0.0,
        last_seen=last_seen,
        hits=hits,
    )


def approaching(*, last_seen: float) -> Track:
    small = Box(500, 200, 600, 400)
    large = Box(470, 140, 630, 460)
    history = deque(
        [
            (0.0, small),
            (0.1, small),
            (0.2, small),
            (0.5, small),
            (0.6, small),
            (0.7, small),
            (0.8, large),
            (0.9, large),
            (1.0, large),
        ]
    )
    return Track(1, large, 0.9, 0.0, last_seen, hits=9, history=history)


def test_unseen_intruder_waits_until_seen_before_starting_alert() -> None:
    engine = alert_engine()
    track = intruder(last_seen=0.9)

    unseen = engine.assess([track], 1.0, FRAME_SIZE)
    track.last_seen = 1.1
    seen = engine.assess([track], 1.1, FRAME_SIZE)

    assert unseen.roles[1] is Role.INTRUDER
    assert unseen.intruders == 1
    assert not unseen.alert_started
    assert unseen.status is Status.CLEAR
    assert seen.alert_started
    assert seen.status is Status.ALERT


def test_unseen_approaching_track_cannot_raise_status_until_seen() -> None:
    engine = alert_engine()
    track = approaching(last_seen=1.0)

    unseen = engine.assess([track], 1.1, FRAME_SIZE)
    track.last_seen = 1.2
    seen = engine.assess([track], 1.2, FRAME_SIZE)

    assert unseen.roles[1] is Role.APPROACHING
    assert not unseen.alert_started
    assert unseen.status is Status.CLEAR
    assert seen.alert_started
    assert seen.status is Status.APPROACHING


def test_quiet_continuation_waits_for_a_seen_intruder() -> None:
    engine = alert_engine()
    first = engine.assess([intruder(last_seen=1.0)], 1.0, FRAME_SIZE)
    cleared_at = 1.0 + ALERT_HOLD_SECONDS
    cleared = engine.assess([], cleared_at, FRAME_SIZE)
    returning = intruder(last_seen=cleared_at, track_id=2)

    unseen = engine.assess([returning], cleared_at + 0.1, FRAME_SIZE)
    returning.last_seen = cleared_at + 0.2
    seen = engine.assess([returning], cleared_at + 0.2, FRAME_SIZE)

    assert first.alert_started
    assert cleared.status is Status.CLEAR
    assert not unseen.continued
    assert unseen.status is Status.CLEAR
    assert seen.continued
    assert seen.status is Status.ALERT


def test_active_alert_is_held_while_intruder_is_temporarily_unseen() -> None:
    engine = alert_engine()
    track = intruder(last_seen=1.0)
    engine.assess([track], 1.0, FRAME_SIZE)

    missed = engine.assess([track], 1.1, FRAME_SIZE)

    assert missed.roles[1] is Role.INTRUDER
    assert missed.status is Status.ALERT
    assert engine.alert_active
    assert not missed.alert_started and not missed.continued
    assert engine.assess([], 2.5, FRAME_SIZE).status is Status.ALERT
    assert engine.assess([], 1.1 + ALERT_HOLD_SECONDS, FRAME_SIZE).status is Status.CLEAR


def test_seen_nonalert_track_cannot_start_alert_for_unseen_intruder() -> None:
    engine = alert_engine()
    stale_intruder = intruder(last_seen=0.9)
    fresh_pending = intruder(last_seen=1.0, track_id=2, hits=1)
    fresh_pending.box = Box(700, 100, 900, 500)

    result = engine.assess([stale_intruder, fresh_pending], 1.0, FRAME_SIZE)

    assert result.roles == {1: Role.INTRUDER, 2: Role.PENDING}
    assert not result.alert_started
    assert result.status is Status.WATCHING
