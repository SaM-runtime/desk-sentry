"""Feedback summaries link explicit alert timestamps without guessing a missing link."""

import pytest
from tools.analyze_diagnostics import marker_statistics


def test_false_and_real_feedback_report_linked_roles_and_height_distribution():
    records = [
        {
            "kind": "analysis",
            "ts": 100.0,
            "alert_ts": 99.9,
            "alert_started": True,
            "tracks": [
                {"role": "intruder", "box": [0, 0.1, 0.2, 0.5]},
                {"role": "self", "box": [0.2, 0.1, 0.8, 0.9]},
            ],
        },
        {"kind": "marker", "ts": 102.0, "marker": "false_alarm", "alert_ts": 99.9, "age_s": 2.1},
        {
            "kind": "analysis",
            "ts": 120.0,
            "alert_started": True,
            "tracks": [{"role": "approaching", "box": [0, 0.1, 0.3, 0.6]}],
        },
        {"kind": "marker", "ts": 123.0, "marker": "real", "alert_ts": 120.0, "age_s": 3.0},
        {"kind": "marker", "ts": 124.0, "marker": "false_alarm", "alert_ts": None, "age_s": None},
    ]
    summary = marker_statistics(records)
    assert summary["false_alarm"]["count"] == 2
    assert summary["false_alarm"]["linked"] == 1
    assert summary["false_alarm"]["roles"] == {"intruder": 1, "self": 1}
    assert summary["false_alarm"]["height_median"] == pytest.approx(0.6)
    assert summary["real"]["count"] == summary["real"]["linked"] == 1
    assert summary["real"]["roles"] == {"approaching": 1}
    assert summary["real"]["height_median"] == 0.5


def test_unknown_or_missing_alert_timestamp_is_not_associated_with_another_alert():
    records = [
        {
            "kind": "analysis",
            "ts": 100.0,
            "alert_started": True,
            "tracks": [{"role": "intruder", "box": [0, 0, 0.2, 0.4]}],
        },
        {"kind": "marker", "ts": 200.0, "marker": "real", "alert_ts": 150.0},
        {"kind": "marker", "ts": 201.0, "marker": "missed_alert"},
    ]
    summary = marker_statistics(records)
    assert summary["real"]["count"] == 1 and summary["real"]["linked"] == 0
    assert summary["real"]["roles"] == {} and summary["real"]["height_median"] is None
    assert summary["false_alarm"]["count"] == 0
