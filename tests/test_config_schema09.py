import json
from dataclasses import replace

import pytest

from desk_sentry import config


def test_schema_less_settings_migrate_to_current_schema_on_save(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"armed": False}), encoding="utf-8")

    settings = config.load(path)
    config.save(settings, path)

    assert settings.schema_version == config.CURRENT_SCHEMA_VERSION
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_unknown_keys_survive_normalize_replace_and_save(tmp_path):
    path = tmp_path / "settings.json"
    unknown = {"plugin_option": {"enabled": True, "threshold": 7}}
    path.write_text(json.dumps({"armed": True, **unknown}), encoding="utf-8")

    settings = replace(config.load(path).normalized(), armed=False)
    config.save(settings, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["armed"] is False
    assert payload["plugin_option"] == unknown["plugin_option"]


def test_future_schema_loads_but_save_preserves_exact_file_bytes(tmp_path):
    path = tmp_path / "settings.json"
    original = b'{\n  "schema_version": 99,\n  "armed": false,\n  "future": "value"\n}\n'
    path.write_bytes(original)

    settings = config.load(path)
    settings.armed = True
    config.save(settings, path)

    assert settings.schema_version == 99
    assert settings.armed is True
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {
                "update_check_enabled": True,
                "update_last_checked": 1_700_000_000,
                "update_ignored_version": "2.1.0",
            },
            (True, 1_700_000_000.0, "2.1.0"),
        ),
        (
            {
                "update_check_enabled": "yes",
                "update_last_checked": -1,
                "update_ignored_version": 210,
            },
            (False, 0.0, ""),
        ),
        (
            {
                "update_check_enabled": 1,
                "update_last_checked": "yesterday",
                "update_ignored_version": None,
            },
            (False, 0.0, ""),
        ),
    ],
)
def test_update_settings_validate_persisted_types(tmp_path, raw, expected):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    settings = config.load(path)

    assert (
        settings.update_check_enabled,
        settings.update_last_checked,
        settings.update_ignored_version,
    ) == expected
