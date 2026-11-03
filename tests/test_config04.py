import json
from pathlib import Path

import pytest

from desk_sentry import config
from desk_sentry.config import MAX_EXE_LENGTH, MAX_TARGET_APPS, Settings


def test_legacy_target_app_migrates_once_to_json_list(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps({"target_app": " LINE.exe ", "target_apps": []}),
        encoding="utf-8",
    )

    loaded = config.load(source)
    saved = tmp_path / "saved.json"
    config.save(loaded, saved)
    raw = json.loads(saved.read_text(encoding="utf-8"))

    assert loaded.target_apps == ("line.exe",)
    assert raw["target_apps"] == ["line.exe"]
    assert "target_app" not in raw


def test_target_apps_normalize_deduplicates_preserves_order_and_caps_values() -> None:
    long_name = "x" * (MAX_EXE_LENGTH + 10)
    raw = [" Chrome.EXE ", "chrome.exe", long_name]
    raw.extend(f"app-{index}.exe" for index in range(10))

    normalized = Settings(target_apps=tuple(raw)).normalized().target_apps

    assert normalized == (
        "chrome.exe",
        "x" * MAX_EXE_LENGTH,
        "app-0.exe",
        "app-1.exe",
        "app-2.exe",
        "app-3.exe",
        "app-4.exe",
        "app-5.exe",
    )
    assert len(normalized) == MAX_TARGET_APPS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  ", ""),
        ("alt+ctrl+h", "Ctrl+Alt+H"),
        ("not-a-hotkey", config.DEFAULT_HIDE_HOTKEY),
    ],
)
def test_hide_hotkey_normalizes_blank_valid_and_invalid_values(raw: str, expected: str) -> None:
    assert Settings(hide_hotkey=raw).normalized().hide_hotkey == expected


def test_hide_hotkey_matching_mirror_hotkey_is_rejected() -> None:
    settings = Settings(mirror_hotkey="alt+ctrl+h", hide_hotkey="CTRL+ALT+H").normalized()

    assert settings.mirror_hotkey == "Ctrl+Alt+H"
    assert settings.hide_hotkey == ""
