"""NOTICE is an extra quiet tier; legacy ALERT starts stay at the same timestamps."""

import pytest

from desk_sentry.detector import Box
from desk_sentry.people import Level
from desk_sentry.sentry import AlertEngine, Policy, Role, Status
from desk_sentry.tracker import Track


def box(cx, height, width=0.06):
    return Box((cx - width / 2) * 1000, (0.7 - height) * 1000, (cx + width / 2) * 1000, 700)


@pytest.mark.parametrize("route", ["side", "rear"])
def test_confirmed_side_entry_and_rear_growth_notice_before_unchanged_alert_start(route):
    engine = AlertEngine(Policy(exclude_self=False, stationary_mute_seconds=0))
    engine.assess([], 0.0, (1000, 1000))
    first = box(0.05, 0.08) if route == "side" else box(0.5, 0.065)
    track = Track(1, first, 0.9, 3.0, 3.0)
    track.history.append((3.0, first))
    observations = []
    for index in range(16):
        now = 3.0 + index / 10
        if route == "side":
            current = box(0.05 + min(index, 10) * 0.025, 0.08 if index < 10 else 0.35)
        else:
            current = box(0.5, 0.065 + index * 0.0013, 0.06 + index * 0.001)
            if index == 15:
                current = box(0.5, 0.35, 0.22)
        if index:
            track.record(now, current, 0.9)
        result = engine.assess([track], now, (1000, 1000))
        observations.append((now, result))
    notices = [now for now, result in observations if result.status is Status.NOTICE]
    starts = [now for now, result in observations if result.alert_started]
    assert notices and min(notices) < starts[0]
    assert starts == ([4.0] if route == "side" else [4.5])
    assert not any(
        result.alert_started or result.continued
        for _, result in observations
        if result.status is Status.NOTICE
    )


@pytest.mark.parametrize("excluded", ["passing", "still", "known"])
def test_passing_still_and_known_tracks_do_not_generate_notice(excluded):
    policy = Policy(exclude_self=False, stationary_mute_seconds=0.1 if excluded == "still" else 0)
    engine = AlertEngine(policy)
    engine.assess([], 0.0, (1000, 1000))
    track = Track(1, box(0.05, 0.2), 0.9, 3.0, 3.0)
    track.history.append((3.0, track.box))
    track.record(3.4, box(0.08, 0.2), 0.9)
    result = engine.assess(
        [track], 3.4, (1000, 1000), identities={1: Level.LOW} if excluded == "known" else None
    )
    assert (
        result.roles[1]
        == {"passing": Role.PASSING, "still": Role.STILL, "known": Role.KNOWN}[excluded]
    )
    assert result.status is Status.WATCHING
    assert result.notice_track_ids == ()


def test_unconfirmed_low_score_and_stale_far_entries_do_not_notice():
    engine = AlertEngine(Policy(exclude_self=False, stationary_mute_seconds=0))
    engine.assess([], 0.0, (1000, 1000))
    track = Track(1, box(0.05, 0.08), 0.2, 3.0, 3.0)
    track.history.append((3.0, track.box))
    track.record(3.1, box(0.08, 0.08), 0.2)
    for now in [3.1, 3.2]:
        assert engine.assess([track], now, (1000, 1000)).status is not Status.NOTICE
