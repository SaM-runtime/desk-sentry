"""Tier replay uses numeric recorded detections and independently expected alert timestamps."""

import pytest
from tools.replay_diagnostics import replay_tiers


def side_entry_records():
    records = [
        {
            "kind": "settings",
            "ts": 1000.0,
            "exclude_self": False,
            "stationary_mute_seconds": 0.0,
            "sensitivity": "medium",
        }
    ]
    records.append(
        {
            "kind": "analysis",
            "ts": 1000.0,
            "mono": 0.0,
            "frame": [1000, 1000],
            "tracks": [],
            "dets": [],
        }
    )
    heights = [0.08] * 9 + [0.10, 0.15, 0.23, 0.32] + [0.38] * 6
    for index, height in enumerate(heights):
        mono = 3.0 + index / 10
        cx = 0.05 + min(index, 10) * 0.025
        records.append(
            {
                "kind": "analysis",
                "ts": 1000.0 + mono,
                "mono": mono,
                "frame": [1000, 1000],
                "tracks": [],
                "dets": [[cx - 0.03, 0.7 - height, cx + 0.03, 0.7, 0.9]],
            }
        )
    return records


def test_tiers_report_notice_lead_action_confirmation_and_exact_start_set():
    result = replay_tiers(side_entry_records(), baseline_alert_starts=[1004.2])
    assert result["alert_count"] == result["baseline_alert_count"] == 1
    assert result["alert_start_symmetric_difference"] == 0
    assert result["notice_count"] == 1
    assert result["lead_median_s"] == pytest.approx(1.1)
    assert result["lead_p10_s"] == result["lead_p90_s"] == result["lead_median_s"]
    assert result["alerts_without_lead"] == 0
    assert result["automatic_action_count"] == 1
    assert result["confirmation_filtered_alerts"] == 0


def test_baseline_mismatch_or_missing_reference_is_not_reported_as_parity():
    changed = replay_tiers(side_entry_records(), baseline_alert_starts=[])
    assert changed["baseline_available"] == 1
    assert changed["alert_start_symmetric_difference"] == 1
    unavailable = replay_tiers(side_entry_records())
    assert unavailable["baseline_available"] == 0
    assert unavailable["alert_start_symmetric_difference"] is None
