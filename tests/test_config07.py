"""New cover and feedback settings are opt-in and cannot collide with older shortcuts."""

from desk_sentry.config import Settings, load, save


def test_cover_and_markers_default_off_and_roundtrip_normalized(tmp_path):
    defaults = Settings()
    assert defaults.cover_app == defaults.mark_false_hotkey == defaults.mark_real_hotkey == ""
    settings = Settings(
        cover_app="  MAIL.EXE ", mark_false_hotkey="Ctrl+Alt+F", mark_real_hotkey="Ctrl+Alt+R"
    ).normalized()
    assert settings.cover_app == "mail.exe"
    path = tmp_path / "settings.json"
    save(settings, path)
    assert load(path) == settings


def test_duplicate_new_hotkeys_are_rejected_without_changing_older_bindings():
    settings = Settings(mark_false_hotkey="Ctrl+Alt+M", mark_real_hotkey="Ctrl+Alt+H").normalized()
    assert settings.mark_false_hotkey == settings.mark_real_hotkey == ""
    assert settings.mirror_hotkey == "Ctrl+Alt+M" and settings.hide_hotkey == "Ctrl+Alt+H"
    settings = Settings(mark_false_hotkey="Ctrl+Alt+F", mark_real_hotkey="Alt+Ctrl+F").normalized()
    assert settings.mark_false_hotkey == "Ctrl+Alt+F" and settings.mark_real_hotkey == ""
    settings = Settings(cover_app=None, mark_false_hotkey="F", mark_real_hotkey=None).normalized()
    assert settings.cover_app == settings.mark_false_hotkey == settings.mark_real_hotkey == ""
    settings = Settings(mirror_hotkey="Ctrl+Alt+B").normalized()
    assert settings.mark_false_hotkey == settings.mark_real_hotkey == ""
