"""Replay experiments stay isolated from runtime rules and reset reboot clocks."""

from copy import deepcopy

import pytest
from tools import replay_diagnostics as replay

from desk_sentry import actions, sentry
from test_replay_tiers07 import side_entry_records


def test_monotonic_rollback_replays_two_sessions_as_two_independent_actions():
    first = side_entry_records()
    second = deepcopy(first)
    for record in second:
        record["ts"] += 10000.0
    assert replay.replay_tiers(first)["automatic_action_count"] == 1
    assert replay.replay_tiers(second)["automatic_action_count"] == 1
    combined = replay.replay_tiers(first + second)
    assert combined["alert_count"] == 2
    assert combined["automatic_action_count"] == 2
    assert combined["cooldown_filtered_alerts"] == 0


def test_variants_use_same_alert_starts_and_restore_runtime_constants():
    records = side_entry_records()
    settings_before = (
        sentry.ENTRY_WINDOW_SECONDS,
        sentry.NOTICE_APPROACH_GROWTH,
        actions.EVADE_DWELL_SECONDS,
        actions.EVADE_NEAR_HEIGHT_MULTIPLIER,
    )
    results = replay.replay_variants(records)
    assert [row["variant"] for row in results] == list(range(8))
    assert all(
        row["alert_count"] == 1 and row["alert_start_symmetric_difference"] == 0 for row in results
    )
    # The scene ends before 0.8 s and never reaches the A3 height 0.30 * 1.3 = 0.39.
    assert [row["automatic_action_count"] for row in results] == [1, 1, 1, 1, 1, 0, 0, 0]
    assert settings_before == (
        sentry.ENTRY_WINDOW_SECONDS,
        sentry.NOTICE_APPROACH_GROWTH,
        actions.EVADE_DWELL_SECONDS,
        actions.EVADE_NEAR_HEIGHT_MULTIPLIER,
    )


@pytest.mark.parametrize("variant", [1, 2, 3])
def test_passing_notice_variants_produce_lead_without_adding_alerts(variant):
    records = side_entry_records()
    # Side entry in the PASSING height band; N0 forbids it, N1/N2/N3 may notice.
    for record in records:
        if record["kind"] == "analysis" and record["dets"] and record["mono"] < 4.0:
            record["dets"][0][1] = 0.5
    results = replay.replay_variants(records)
    assert results[0]["alerts_with_lead"] == 0
    assert results[variant]["alerts_with_lead"] == 1
    assert results[variant]["lead_median_s"] >= 1.0
    assert results[variant]["alert_start_symmetric_difference"] == 0


def test_n2_extends_only_notice_entry_window_for_delayed_inward_movement():
    records = side_entry_records()
    for record in records:
        if record["kind"] != "analysis" or not record["dets"]:
            continue
        index = round((record["mono"] - 3.0) * 10)
        center = 0.05 + max(0, min(index - 6, 10)) * 0.025
        record["dets"][0][0], record["dets"][0][2] = center - 0.03, center + 0.03
        if record["mono"] < 4.0:
            record["dets"][0][1] = 0.5
    results = replay.replay_variants(records)
    assert results[1]["alerts_with_lead"] == 0
    assert results[2]["alerts_with_lead"] == 1
    assert results[2]["lead_median_s"] > 0
    assert all(row["alert_start_symmetric_difference"] == 0 for row in results)


def test_n3_relaxes_height_growth_without_relaxing_alerts_or_width_requirement():
    records = side_entry_records()[:2]
    for index in range(11):
        height, width = 0.065 + 0.0006 * index, 0.06 + 0.002 * index
        records.append(
            {
                "kind": "analysis",
                "ts": 1003.0 + index / 10,
                "mono": 3.0 + index / 10,
                "frame": [1000, 1000],
                "tracks": [],
                "dets": [[0.5 - width / 2, 0.7 - height, 0.5 + width / 2, 0.7, 0.9]],
            }
        )
    results = replay.replay_variants(records)
    assert results[1]["notice_count"] == 0
    assert results[3]["notice_count"] == 1
    assert all(
        row["alert_count"] == 0 and row["alert_start_symmetric_difference"] == 0 for row in results
    )
    for record in records:
        if record["kind"] == "analysis" and record["dets"]:
            record["dets"][0][0], record["dets"][0][2] = 0.47, 0.53
    assert replay.replay_variants(records)[3]["notice_count"] == 0
