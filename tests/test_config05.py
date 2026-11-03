import json

import pytest

from desk_sentry import config


def test_old_settings_load_new_safe_defaults_and_new_fields_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"target_apps": ["mail.exe"], "action": "minimize_app"}))
    old = config.load(path)
    assert (old.away_action, old.away_minutes, old.hide_hotkey_style) == ("none", 5, "minimize")
    assert old.pause_when_locked and not old.schedule_enabled
    assert (old.schedule_start, old.schedule_end, old.schedule_days) == (
        "08:30",
        "18:30",
        (0, 1, 2, 3, 4),
    )
    old.schedule_enabled = True
    old.action = "curtain"
    old.away_action = "lock"
    old.hide_hotkey_style = "curtain"
    old.schedule_start = "22:00"
    old.schedule_end = "06:00"
    old.schedule_days = (0, 4)
    config.save(old, path)
    assert config.load(path) == old


@pytest.mark.parametrize("minutes,expected", [(-3, 1), (100, 60), ("broken", 5)])
def test_invalid_new_settings_normalize_without_losing_other_values(tmp_path, minutes, expected):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "away_minutes": minutes,
                "away_action": "lock",
                "hide_hotkey_style": "bad",
                "schedule_start": "8:30",
                "schedule_end": "24:00",
                "schedule_days": [6, 0, 0, True, -1, 8, "2"],
            }
        )
    )
    settings = config.load(path)
    assert settings.away_minutes == expected
    assert settings.away_action == "lock"
    assert settings.hide_hotkey_style == "minimize"
    assert (settings.schedule_start, settings.schedule_end) == ("08:30", "18:30")
    assert settings.schedule_days == (0, 6)
