"""Discreet settings have explicit defaults and reject malformed persisted values."""

import json

import pytest

from desk_sentry import config
from desk_sentry.config import Settings


def test_discreet_settings_defaults_and_roundtrip(tmp_path):
    defaults = Settings()
    assert (defaults.discreet, defaults.discreet_cue, defaults.discreet_corner) == (
        False,
        "dot",
        "top-right",
    )
    selected = Settings(discreet=True, discreet_cue="line", discreet_corner="top-left")
    path = tmp_path / "settings.json"
    config.save(selected, path)
    loaded = config.load(path)
    assert loaded == selected.normalized()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["discreet"] is True
    assert payload["discreet_cue"] == "line"


@pytest.mark.parametrize("invalid", [None, "yes", 1, [], {}])
def test_invalid_discreet_values_fall_back_without_altering_existing_preferences(tmp_path, invalid):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "discreet": invalid,
                "discreet_cue": invalid,
                "discreet_corner": invalid,
                "show_mirror": True,
                "sound": True,
                "edge_flash": True,
                "compact_mode": True,
            }
        ),
        encoding="utf-8",
    )
    loaded = config.load(path)
    assert (loaded.discreet, loaded.discreet_cue, loaded.discreet_corner) == (
        False,
        "dot",
        "top-right",
    )
    assert loaded.show_mirror and loaded.sound and loaded.edge_flash and loaded.compact_mode
