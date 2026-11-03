import pytest

from desk_sentry.detector import Box, Detection
from desk_sentry.tracker import Tracker, boxes_overlap


def test_keeps_id_for_fast_walker_without_overlap() -> None:
    tracker = Tracker()
    ids = []
    for i in range(5):
        x = 100 + 70 * i  # moves most of a box width per frame
        tracks = tracker.update([Detection(Box(x, 100, x + 80, 400), 0.9)], i * 0.1)
        ids.append(tracks[0].track_id)
    assert len(set(ids)) == 1
    assert tracks[0].hits == 5


def test_two_people_keep_separate_ids() -> None:
    tracker = Tracker()
    for i in range(4):
        tracker.update(
            [
                Detection(Box(100 + 5 * i, 100, 200 + 5 * i, 400), 0.9),
                Detection(Box(800 - 5 * i, 100, 900 - 5 * i, 400), 0.9),
            ],
            i * 0.1,
        )
    assert sorted(t.track_id for t in tracker.tracks) == [1, 2]
    assert all(t.hits == 4 for t in tracker.tracks)


def test_expires_after_ttl() -> None:
    tracker = Tracker(ttl_seconds=0.5)
    tracker.update([Detection(Box(0, 0, 10, 10), 0.9)], 0.0)
    assert tracker.update([], 0.4)
    assert tracker.update([], 0.6) == []


def test_growth_and_displacement() -> None:
    tracker = Tracker()
    for i in range(11):
        h = 100 + 5 * i
        tracks = tracker.update(
            [Detection(Box(100 + 10 * i, 100, 150 + 10 * i, 100 + h), 0.9)], i * 0.1
        )
    track = tracks[0]
    assert track.growth(1.0) > 1.3
    assert track.displacement(1.0) > 0.5


def test_jitter_is_not_motion() -> None:
    tracker = Tracker()
    for i in range(20):
        j = 12 if i % 2 else -12  # +/-12 % height jitter on a static person
        tracks = tracker.update([Detection(Box(300, 200 - j, 400, 400 + j), 0.8)], i * 0.1)
    track = tracks[0]
    assert abs(track.growth(1.0) - 1.0) < 0.1
    assert track.displacement(1.0) < 0.1


def test_young_track_reports_no_motion() -> None:
    tracker = Tracker()
    tracker.update([Detection(Box(0, 0, 50, 100), 0.9)], 0.0)
    track = tracker.update([Detection(Box(0, 0, 80, 160), 0.9)], 0.2)[0]
    assert track.growth(1.0) == 1.0 and track.displacement(1.0) == 0.0


def test_prediction_bridges_a_missed_frame_for_fast_walker() -> None:
    tracker = Tracker()
    for i in range(3):
        x = 100 + 120 * i
        tracker.update([Detection(Box(x, 100, x + 60, 280), 0.9)], i * 0.1)
    tracker.update([], 0.3)  # missed detection
    x = 100 + 120 * 4  # 480 px on: far beyond plain IoU / centre-distance reach
    tracks = tracker.update([Detection(Box(x, 100, x + 60, 280), 0.9)], 0.4)
    assert [t.track_id for t in tracks] == [1]


def test_nested_duplicate_boxes_stay_separate_tracks() -> None:
    # Alerting must see every box; one card per person is the view's job.
    tracker = Tracker()
    full = Detection(Box(100, 100, 200, 400), 0.9)
    upper = Detection(Box(120, 100, 180, 180), 0.8)
    tracks = tracker.update([full, upper], 0.0)
    assert [t.track_id for t in tracks] == [1, 2]


def test_track_born_after_a_loss_resumes_the_lost_track() -> None:
    tracker = Tracker()
    for i in range(7):  # walking right at 1.5 box heights per second
        x = 30 * i
        tracker.update([Detection(Box(x, 0, x + 100, 200), 0.8)], i / 10)
    tracker.update([], 0.7)
    # 1.2 box heights from the prediction: beyond plain association, a fresh
    # track would otherwise sit next to the lost one for the whole TTL.
    far = Box(480, 0, 580, 200)
    tracks = tracker.update([Detection(far, 0.9)], 0.8)
    assert [t.track_id for t in tracks] == [1]
    survivor = tracks[0]
    assert survivor.first_seen == 0.0 and survivor.hits == 8
    assert survivor.box == far and survivor.last_seen == 0.8


@pytest.mark.parametrize("reverse", [False, True])
def test_lost_track_resumes_on_the_closest_newcomer(reverse: bool) -> None:
    tracker = Tracker()
    for i in range(7):  # walking right
        x = 30 * i
        tracker.update([Detection(Box(x, 0, x + 100, 200), 0.9)], i / 10)
    near, far = Box(430, 0, 530, 200), Box(530, 0, 630, 200)
    detections = [Detection(far, 0.9), Detection(near, 0.9)]
    tracks = tracker.update(detections[::-1] if reverse else detections, 0.7)
    assert {t.track_id: t.box for t in tracks}[1] == near
    assert len(tracks) == 2


def test_still_track_is_not_resumed_by_a_nearby_newcomer() -> None:
    # A seated user's box drops out and someone appears beside the seat: the
    # newcomer must not inherit the user's identity (and self-exclusion).
    tracker = Tracker()
    user = Box(0, 0, 100, 200)
    tracker.update([Detection(user, 0.9)], 0.0)
    tracker.update([Detection(user, 0.9)], 0.1)
    tracks = tracker.update([Detection(Box(220, 0, 320, 200), 0.9)], 0.2)
    assert sorted(t.track_id for t in tracks) == [1, 2]


def test_established_track_is_never_merged_into_a_lost_one() -> None:
    tracker = Tracker()
    for now, x in ((0.0, 0), (0.1, 20), (0.2, 600), (0.3, 450), (0.4, 300)):
        tracks = tracker.update([Detection(Box(x, 0, x + 100, 200), 0.9)], now)
    assert [t.track_id for t in tracks if t.last_seen == 0.4] == [2]


def test_jittering_seated_user_is_not_resumed_by_a_passer_by() -> None:
    tracker = Tracker()
    for i in range(7):  # box jitters by 10 % of its height every sample
        x = 20 * (i % 2)
        tracker.update([Detection(Box(x, 0, x + 100, 200), 0.9)], i / 10)
    tracks = tracker.update([Detection(Box(300, 0, 400, 200), 0.9)], 0.8)
    assert sorted(t.track_id for t in tracks) == [1, 2]


def test_walker_gone_for_longer_than_a_few_frames_is_not_resumed() -> None:
    tracker = Tracker()
    for i in range(7):
        x = 30 * i
        tracker.update([Detection(Box(x, 0, x + 100, 200), 0.9)], i / 10)
    tracks = tracker.update([Detection(Box(600, 0, 700, 200), 0.9)], 1.8)
    assert [t.track_id for t in tracks if t.last_seen == 1.8] == [2]


def test_small_person_behind_a_lost_user_track_gets_their_own_track() -> None:
    tracker = Tracker()
    tracker.update([Detection(Box(400, 250, 880, 720), 0.9)], 0.0)
    tracks = tracker.update([Detection(Box(560, 150, 650, 400), 0.8)], 0.1)
    assert [t.track_id for t in tracks] == [1, 2]


def test_tracks_seen_together_are_never_merged() -> None:
    tracker = Tracker()
    a, b = Box(0, 0, 100, 200), Box(60, 0, 160, 200)
    tracker.update([Detection(a, 0.9), Detection(b, 0.8)], 0.0)
    tracks = tracker.update([Detection(b, 0.8)], 0.1)
    assert sorted(t.track_id for t in tracks) == [1, 2]


def test_expired_track_cannot_take_over_new_detection() -> None:
    tracker = Tracker(ttl_seconds=0.5)
    detection = Detection(Box(0, 0, 100, 200), 0.9)
    tracker.update([detection], 0.0)
    tracks = tracker.update([detection], 0.6)
    assert [t.track_id for t in tracks] == [2]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Box(0, 0, 13, 10), Box(7, 0, 20, 10), True),  # IoU exactly .3
        (Box(0, 0, 13, 10), Box(7.01, 0, 20.01, 10), False),
        (Box(0, 0, 10, 10), Box(4, 0, 104, 10), True),  # smaller ratio exactly .6
        (Box(0, 0, 10, 10), Box(4.01, 0, 104.01, 10), False),
        (Box(0, 0, 0, 10), Box(0, 0, 10, 10), False),
        (Box(0, 0, 10, 10), Box(10, 0, 20, 10), False),
    ],
)
def test_overlap_boundaries_are_inclusive_and_symmetric(a: Box, b: Box, expected: bool) -> None:
    assert boxes_overlap(a, b) is expected
    assert boxes_overlap(b, a) is expected
