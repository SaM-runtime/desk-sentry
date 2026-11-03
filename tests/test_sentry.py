from desk_sentry.detector import Box, Detection
from desk_sentry.sentry import (
    ALERT_HOLD_SECONDS,
    WARMUP_SECONDS,
    AlertEngine,
    Policy,
    Role,
    Status,
)
from desk_sentry.tracker import Tracker

W, H = 1280, 720
USER = Box(400, 250, 880, 720)  # large, bottom-centre: the person at the desk
ZONE = (0.25, 0.25, 0.75, 1.0)


def det(box: Box, score: float = 0.8) -> Detection:
    return Detection(box, score)


def walker(t: float, x0: float = 100.0, speed: float = 300.0) -> Box:
    x = x0 + speed * t
    return Box(x, 150, x + 90, 450)


def armed(policy: Policy) -> AlertEngine:
    """Engine that was armed on an empty scene before the test's t=0."""

    engine = AlertEngine(policy)
    engine.assess([], -3.0, (W, H))  # warm-up over by t=-1; "away" not before t=12
    return engine


def run(engine: AlertEngine, tracker: Tracker, frames: list[tuple[float, list[Detection]]]):
    results = []
    for now, dets in frames:
        results.append(engine.assess(tracker.update(dets, now), now, (W, H)))
    return results


def test_empty_scene_is_clear() -> None:
    engine = armed(Policy(self_zone=ZONE))
    result = engine.assess([], 0.0, (W, H))
    assert result.status is Status.CLEAR and not result.alert_started


def test_user_in_self_zone_never_alerts() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    results = run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(30)])
    assert all(r.status is Status.CLEAR for r in results)
    assert all(role is Role.SELF for r in results for role in r.roles.values())


def test_person_walking_behind_raises_alert_within_two_frames() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = [(i * 0.1, [det(USER), det(walker(i * 0.1))]) for i in range(5)]
    results = run(engine, tracker, frames)
    assert results[0].status is not Status.ALERT  # single frame is only "pending"
    assert results[1].alert_started and results[1].status is Status.ALERT
    assert sum(r.alert_started for r in results) == 1
    assert results[-1].intruders == 1


def test_small_person_behind_user_overlapping_zone_is_not_self() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    behind = Box(560, 150, 650, 400)  # inside the zone, but much smaller than the user
    results = run(engine, tracker, [(i * 0.1, [det(USER), det(behind)]) for i in range(3)])
    assert results[-1].intruders == 1


def test_alert_clears_after_hold_time() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker(ttl_seconds=0.3)
    frames = [(i * 0.1, [det(walker(i * 0.1))]) for i in range(4)]
    frames += [(0.4 + i * 0.1, []) for i in range(int(ALERT_HOLD_SECONDS * 10) + 6)]
    results = run(engine, tracker, frames)
    assert results[3].status is Status.ALERT
    assert results[-1].status is Status.CLEAR


def test_stationary_person_is_muted_then_realerts_when_moving() -> None:
    engine = armed(Policy(self_zone=ZONE, stationary_mute_seconds=2.0))
    tracker = Tracker()
    seated = Box(100, 200, 200, 420)
    frames = [(i * 0.1, [det(seated)]) for i in range(60)]
    results = run(engine, tracker, frames)
    assert results[5].status is Status.ALERT
    assert results[-1].status is Status.WATCHING
    assert results[-1].roles[1] is Role.STILL

    moving = [(6.0 + i * 0.1, [det(Box(100 + 40 * i, 200, 200 + 40 * i, 420))]) for i in range(8)]
    later = run(engine, tracker, moving)
    # Within 20 s of the last alert: shown again, but as a quiet continuation.
    assert any(r.continued for r in later)
    assert later[-1].status in (Status.ALERT, Status.APPROACHING)


def test_approaching_person_escalates() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = []
    for i in range(15):
        grow = 1 + 0.05 * i  # near (>= 30 % of the frame) and scaling up uniformly
        half = 120 * grow
        frames.append((i * 0.1, [det(Box(900, 330 - half, 900 + half * 0.6, 330 + half))]))
    results = run(engine, tracker, frames)
    assert results[-1].status is Status.APPROACHING


def test_far_person_is_ignored_at_low_sensitivity() -> None:
    engine = armed(Policy(sensitivity="low", self_zone=ZONE))
    tracker = Tracker()
    tiny = [(i * 0.1, [det(Box(100 + 10 * i, 100, 130 + 10 * i, 160))]) for i in range(5)]
    results = run(engine, tracker, tiny)
    assert all(r.intruders == 0 for r in results)
    assert results[-1].roles[1] is Role.FAR


def test_auto_calibration_finds_user_without_alerting() -> None:
    policy = Policy(self_zone=None)
    engine, tracker = armed(policy), Tracker()
    results = run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(25)])
    assert all(r.intruders == 0 for r in results)
    calibrated = [r.calibrated_zone for r in results if r.calibrated_zone]
    assert len(calibrated) == 1
    x1, y1, x2, y2 = calibrated[0]
    assert x1 < USER.x1 / W and x2 > USER.x2 / W
    assert policy.self_zone == calibrated[0]


def test_user_standing_up_stays_self() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = [(i * 0.1, [det(USER)]) for i in range(3)]
    frames += [(0.3 + i * 0.1, [det(Box(420, 250 - 30 * i, 860, 720))]) for i in range(6)]
    results = run(engine, tracker, frames)
    assert all(r.intruders == 0 for r in results)


def test_self_exclusion_disabled_counts_everyone() -> None:
    engine, tracker = armed(Policy(exclude_self=False, self_zone=ZONE)), Tracker()
    results = run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(3)])
    assert results[-1].intruders == 1


def test_auto_calibration_prefers_centred_person_over_larger_edge_object() -> None:
    policy = Policy(self_zone=None)
    engine, tracker = armed(policy), Tracker()
    edge = Box(0, 0, 400, 715)  # bigger, but hugging the left edge (coat on a chair, door...)
    user = Box(540, 290, 1050, 720)
    run(engine, tracker, [(i * 0.1, [det(edge), det(user)]) for i in range(25)])
    assert policy.self_zone is not None
    assert policy.self_zone[0] > 0.3


def test_zone_for_point_picks_smallest_containing_box() -> None:
    from desk_sentry.sentry import zone_for_point

    big, small = Box(0.1, 0.1, 0.9, 1.0), Box(0.4, 0.3, 0.6, 0.7)
    zone = zone_for_point([big, small], 0.5, 0.5)
    assert zone is not None and zone[0] > 0.3 and zone[2] < 0.7
    assert zone_for_point([small], 0.05, 0.05) is None


def test_people_present_when_arming_must_move_before_alerting() -> None:
    engine, tracker = AlertEngine(Policy(self_zone=ZONE)), Tracker()
    coat = Box(0, 0, 270, 715)  # static false positive at the frame edge
    results = run(engine, tracker, [(i * 0.1, [det(USER), det(coat)]) for i in range(40)])
    assert all(r.intruders == 0 for r in results)
    later = [(4.0 + i * 0.1, [det(USER), det(Box(40 * i, 0, 270 + 40 * i, 715))]) for i in range(9)]
    assert any(r.alert_started for r in run(engine, tracker, later))


def test_person_entering_after_warmup_alerts() -> None:
    engine, tracker = AlertEngine(Policy(self_zone=ZONE)), Tracker()
    run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(int(WARMUP_SECONDS * 10) + 1)])
    t0 = WARMUP_SECONDS + 0.2
    frames = [(t0 + i * 0.1, [det(USER), det(walker(i * 0.1))]) for i in range(3)]
    assert any(r.alert_started for r in run(engine, tracker, frames))


def test_flickering_static_detection_does_not_realert() -> None:
    engine = armed(Policy(self_zone=ZONE, stationary_mute_seconds=1.0))
    tracker = Tracker(ttl_seconds=0.5)
    poster = Box(1000, 100, 1100, 400)
    first = run(engine, tracker, [(i * 0.1, [det(poster)]) for i in range(20)])
    assert first[-1].roles[1] is Role.STILL
    gap = run(engine, tracker, [(2.0 + i * 0.1, []) for i in range(8)])  # track lost
    assert not tracker.tracks and gap[-1].status is Status.CLEAR
    back = run(engine, tracker, [(3.0 + i * 0.1, [det(poster)]) for i in range(5)])
    assert all(not r.alert_started and r.intruders == 0 for r in back)


def test_detection_without_pixel_motion_is_treated_as_static() -> None:
    """A clothes rack whose score flickers around the threshold must not alert."""

    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    rack = Box(1000, 150, 1100, 450)
    results = run_with_motion(engine, tracker, [(i * 0.1, [det(rack)]) for i in range(12)], 0.0)
    assert all(r.intruders == 0 for r in results)
    assert results[-1].roles[1] is Role.STILL


def test_walker_with_pixel_motion_alerts_immediately() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = [(i * 0.1, [det(walker(i * 0.1))]) for i in range(3)]
    results = run_with_motion(engine, tracker, frames, 0.3)
    assert results[1].alert_started


def test_static_thing_starts_moving_then_alerts() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    still = [(i * 0.1, [det(Box(1000, 150, 1100, 450))]) for i in range(10)]
    run_with_motion(engine, tracker, still, 0.0)
    moving = [(1.0 + i * 0.1, [det(Box(1000 - 50 * i, 150, 1100 - 50 * i, 450))]) for i in range(8)]
    assert any(r.alert_started for r in run_with_motion(engine, tracker, moving, 0.3))


def aisle_walker(t: float) -> Box:
    """A colleague crossing an aisle far behind: ~15 % of the frame, walking sideways."""

    x = 900 + 250 * t
    return Box(x, 250, x + 45, 360)


def test_far_passerby_is_shown_but_does_not_alert() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = [(i * 0.1, [det(USER), det(aisle_walker(i * 0.1))]) for i in range(15)]
    results = run(engine, tracker, frames)
    assert all(r.intruders == 0 and not r.alert_started for r in results)
    assert Role.PASSING in results[-1].roles.values()
    assert results[-1].status is Status.WATCHING


def test_far_passerby_alerts_when_opted_in() -> None:
    engine = armed(Policy(self_zone=ZONE, alert_far_passersby=True))
    tracker = Tracker()
    frames = [(i * 0.1, [det(USER), det(aisle_walker(i * 0.1))]) for i in range(5)]
    assert any(r.alert_started for r in run(engine, tracker, frames))


def test_far_person_walking_up_alerts_once_near() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    frames = []
    for i in range(30):  # from ~15 % to ~45 % of the frame height over 3 s
        h = 110 + 7.5 * i
        frames.append((i * 0.1, [det(USER), det(Box(150, 400 - h, 150 + h * 0.4, 400))]))
    results = run(engine, tracker, frames)
    first = next(i for i, r in enumerate(results) if r.alert_started)
    assert 110 + 7.5 * first >= 0.3 * H  # alerted as soon as it counted as near
    assert first < 25


def test_far_colleague_standing_up_is_not_approaching() -> None:
    engine = armed(Policy(self_zone=ZONE, alert_far_passersby=True))
    tracker = Tracker()
    frames = []
    for i in range(15):  # height doubles, width constant, feet stay put
        h = 150 + 12 * i
        frames.append((i * 0.1, [det(USER), det(Box(1000, 500 - h, 1060, 500))]))
    results = run(engine, tracker, frames)
    assert all(r.status is not Status.APPROACHING for r in results)


def test_user_leaving_the_desk_pauses_alerts() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker(ttl_seconds=0.3)
    run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(10)])
    empty = run(engine, tracker, [(1.0 + i * 0.5, []) for i in range(40)])
    assert empty[-1].status is Status.AWAY
    t0 = 21.5
    visitors = run(engine, tracker, [(t0 + i * 0.1, [det(walker(i * 0.1))]) for i in range(5)])
    assert all(r.intruders == 0 and not r.alert_started for r in visitors)
    assert visitors[-1].status is Status.AWAY
    back = run(engine, tracker, [(t0 + 1 + i * 0.1, [det(USER)]) for i in range(5)])
    assert back[-1].status is Status.CLEAR


def test_empty_chair_does_not_count_as_user_present() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker()
    chair = Box(420, 330, 860, 720)  # low-score "person" in the seat all night
    results = run(engine, tracker, [(i * 0.2, [det(chair, 0.38)]) for i in range(100)])
    assert results[-1].status is Status.AWAY


def test_new_box_where_the_user_stood_up_is_still_the_user() -> None:
    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker(ttl_seconds=0.2)
    run(engine, tracker, [(i * 0.1, [det(USER)]) for i in range(5)])
    # Tracker loses the user; a taller box appears where they were and walks off.
    frames = [(0.8 + i * 0.1, [det(Box(380 - 80 * i, 20, 820 - 80 * i, 720))]) for i in range(8)]
    results = run(engine, tracker, frames)
    assert all(r.intruders == 0 for r in results)


def run_with_motion(engine, tracker, frames, fraction):
    return [
        engine.assess(tracker.update(d, t), t, (W, H), motion=lambda _b: fraction)
        for t, d in frames
    ]


def test_realert_within_quiet_window_is_a_continuation() -> None:
    from desk_sentry.sentry import REALERT_QUIET_SECONDS

    engine, tracker = armed(Policy(self_zone=ZONE)), Tracker(ttl_seconds=0.3)
    first = run(engine, tracker, [(i * 0.1, [det(USER), det(walker(i * 0.1))]) for i in range(4)])
    assert sum(r.alert_started for r in first) == 1
    gap = run(engine, tracker, [(0.4 + i * 0.1, [det(USER)]) for i in range(25)])
    assert gap[-1].status is Status.CLEAR
    t0 = 3.0
    again = run(
        engine, tracker, [(t0 + i * 0.1, [det(USER), det(walker(i * 0.1))]) for i in range(4)]
    )
    assert not any(r.alert_started for r in again)
    assert sum(r.continued for r in again) == 1
    assert again[-1].status is Status.ALERT
    quiet_until = 3.4 + REALERT_QUIET_SECONDS + 2
    steps = int((quiet_until - 3.4) / 0.5)
    run(engine, tracker, [(3.4 + i * 0.5, [det(USER)]) for i in range(steps)])
    fresh = run(
        engine,
        tracker,
        [(quiet_until + i * 0.1, [det(USER), det(walker(i * 0.1))]) for i in range(4)],
    )
    assert sum(r.alert_started for r in fresh) == 1


def test_one_lucky_frame_does_not_confirm_a_low_score_track() -> None:
    engine = armed(Policy(sensitivity="high", self_zone=ZONE))  # min score 0.32
    tracker = Tracker()
    scores = [0.25, 0.25, 0.25, 0.25, 0.45]  # one frame well over the 0.32 threshold
    frames = [(i * 0.1, [det(USER), det(walker(i * 0.1), s)]) for i, s in enumerate(scores)]
    results = run(engine, tracker, frames)
    assert all(r.intruders == 0 for r in results)
