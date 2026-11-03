"""User-reported arm boxes stay transient SELF without claiming the seated identity."""

import pytest

from desk_sentry.detector import Box, Detection
from desk_sentry.sentry import AlertEngine, Policy, Role, Status
from desk_sentry.tracker import Track, Tracker

ZONE = (0.404, 0.270, 0.830, 1.0)
ME = (0.517, 0.439, 0.927, 1.0)
ARM = (0.849, 0.722, 0.998, 0.987)


def box(coords):
    return Box(*(v * 1000 for v in coords))


def engine():
    result = AlertEngine(Policy(sensitivity="high", self_zone=ZONE, stationary_mute_seconds=0))
    result.assess([], -3, (1000, 1000))
    return result


@pytest.mark.parametrize(
    "me,arm,score,age",
    [
        (ME, ARM, 0.26, 0.12),
        ((0.553, 0.423, 1.0, 1.0), (0.788, 0.54, 0.907, 0.904), 0.486, 2.67),
        ((0.501, 0.349, 0.998, 0.999), (0.857, 0.62, 0.999, 0.99), 0.442, 0.16),
    ],
)
def test_reported_arm_boxes_are_self_not_intruders(me, arm, score, age):
    rules = engine()
    user = Track(1, box(me), 0.95, -2, 0, hits=20)
    hand = Track(2, box(arm), score, -age, 0, hits=2)
    hand.scores.extend([0.8, 0.8])  # valid previous scores can confirm a low current score
    result = rules.assess([hand, user], 0, (1000, 1000))
    assert result.roles == {1: Role.SELF, 2: Role.SELF}
    assert not result.alert_started and result.intruders == 0
    assert rules._self_ids == {1}
    assert rules._last_self_seen == 0
    assert rules._last_self_box == user.box


@pytest.mark.parametrize("score,age,hits", [(0.99, 30, 100), (0.10, 0, 1)])
def test_arm_rule_precedes_pending_and_ignores_score_and_age(score, age, hits):
    rules = engine()
    user = Track(1, box(ME), 0.95, -2, 0, hits=20)
    arm = Track(2, box(ARM), score, -age, 0, hits=hits)
    result = rules.assess([user, arm], 0, (1000, 1000))
    assert result.roles[2] is Role.SELF
    assert rules._self_ids == {1}


@pytest.mark.parametrize(
    "visitor",
    [
        (0.85, 0.20, 0.99, 0.99),  # full person, head above user's head
        (0.05, 0.50, 0.25, 0.99),  # left, not beside the user's box
        (0.05, 0.10, 0.15, 0.40),  # distant person still tall enough at high sensitivity
        (0.85, 0.50, 0.99, 0.84),  # lower edge does not reach the bottom region
        (0.85, 0.48, 0.99, 0.99),  # upper edge too close even at the smallest scanned top gap
    ],
)
def test_real_people_outside_limb_geometry_still_alert(visitor):
    rules, tracker = engine(), Tracker()
    results = [
        rules.assess(
            tracker.update([Detection(box(ME), 0.95), Detection(box(visitor), 0.9)], now),
            now,
            (1000, 1000),
        )
        for now in (0, 0.1)
    ]
    assert results[-1].alert_started
    assert results[-1].intruders == 1


def test_growing_arm_recovers_general_classification_on_first_nonmatching_frame():
    rules = engine()
    user = Track(1, box(ME), 0.95, -2, 0, hits=20)
    visitor = Track(2, box(ARM), 0.95, -1, 0, hits=2)
    assert rules.assess([user, visitor], 0, (1000, 1000)).roles[2] is Role.SELF
    assert rules._self_ids == {1}
    user.record(0.1, box(ME), 0.95)
    visitor.record(0.1, box((0.85, 0.20, 0.998, 0.99)), 0.95)
    result = rules.assess([visitor, user], 0.1, (1000, 1000))
    assert result.roles[2] is Role.INTRUDER
    assert result.alert_started
    assert rules._self_ids == {1}


@pytest.mark.parametrize("stale_user", [False, True])
def test_no_same_frame_self_means_no_limb_exclusion(stale_user):
    rules = engine()
    user = Track(1, box(ME), 0.95, -2, 0, hits=20)
    rules.assess([user], 0, (1000, 1000))
    visitor = Track(2, box(ARM), 0.95, -1, 0.1, hits=2)
    tracks = [visitor, user] if stale_user else [visitor]
    result = rules.assess(tracks, 0.1, (1000, 1000))
    assert result.roles[2] is Role.INTRUDER
    assert result.alert_started


def test_arm_does_not_refresh_last_seen_box_or_prevent_away():
    rules = engine()
    user = Track(1, box(ME), 0.95, -2, 0, hits=20)
    rules.assess([user], 0, (1000, 1000))
    user.record(1, box(ME), 0.5)  # low-confidence chair must not refresh presence
    arm = Track(2, box(ARM), 0.99, 0, 1, hits=10)
    result = rules.assess([arm, user], 1, (1000, 1000))
    assert result.roles[2] is Role.SELF
    assert rules._last_self_seen == 0
    assert rules._last_self_box == box(ME)
    assert rules._self_ids == {1}
    arm.record(16, box(ARM), 0.99)
    result = rules.assess([arm], 16, (1000, 1000))
    assert result.roles[2] is not Role.SELF
    assert result.status is Status.AWAY
    assert rules._last_self_seen == 0
    assert rules._last_self_box == box(ME)


def test_self_exclusion_disabled_does_not_hide_arms():
    rules, tracker = engine(), Tracker()
    rules.policy.exclude_self = False
    for now in (0, 0.1):
        result = rules.assess(
            tracker.update([Detection(box(ME), 0.95), Detection(box(ARM), 0.99)], now),
            now,
            (1000, 1000),
        )
    assert result.intruders == 2
    assert Role.SELF not in result.roles.values()


def test_tracker_retains_both_user_and_arm_detections():
    rules, tracker = engine(), Tracker()
    for now in (0, 0.1):
        tracks = tracker.update([Detection(box(ME), 0.95), Detection(box(ARM), 0.99)], now)
        assert len(tracks) == 2 and {t.track_id for t in tracks} == {1, 2}
        result = rules.assess(tracks, now, (1000, 1000))
        assert len(tracks) == 2 and {t.track_id for t in tracks} == {1, 2}
        assert result.roles == {1: Role.SELF, 2: Role.SELF}
