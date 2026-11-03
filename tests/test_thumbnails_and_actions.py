from pathlib import Path

import numpy as np
from pytestqt.qtbot import QtBot

from desk_sentry import actions, config, hotkey
from desk_sentry.config import Settings
from desk_sentry.detector import Box
from desk_sentry.engine import alert_thumbnail
from desk_sentry.ui.history import AlertHistory, HistoryWindow


def test_thumbnail_is_cropped_mirrored_and_small() -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[100:500, 100:150] = (0, 0, 255)  # BGR red: the person's left half
    frame[100:500, 150:200] = (255, 0, 0)  # BGR blue: the right half
    thumb = alert_thumbnail(frame, [Box(100, 100, 200, 500)])
    assert thumb is not None and thumb.shape[0] <= 160 and thumb.shape[1] <= 240
    row = thumb[thumb.shape[0] // 2]
    red = np.where(row[:, 0] > 200)[0]  # RGB now
    blue = np.where(row[:, 2] > 200)[0]
    assert red.mean() > blue.mean()  # mirrored: red moved to the right
    assert alert_thumbnail(frame, []) is None


def test_history_is_bounded_and_tracks_unseen() -> None:
    history = AlertHistory(maxlen=10)
    for _ in range(15):
        history.add(np.zeros((160, 240, 3), np.uint8))
    assert len(history) == 10 and history.unseen == 10
    assert history.nbytes <= 1_200_000
    history.mark_seen()
    assert history.unseen == 0
    history.clear()
    assert len(history) == 0


def test_history_window_renders_and_clears(qtbot: QtBot) -> None:
    history = AlertHistory()
    window = HistoryWindow(history)
    qtbot.addWidget(window)
    window.open_near(None)
    assert window.empty_label.isVisibleTo(window) and not window.clear_button.isEnabled()
    history.add(np.zeros((100, 60, 3), np.uint8))
    history.add(np.zeros((120, 80, 3), np.uint8))
    window.open_near(None)
    assert not window.empty_label.isVisibleTo(window) and history.unseen == 0
    window.clear_button.click()
    assert len(history) == 0


def test_minimize_app_matches_program_not_title() -> None:
    windows = [
        actions.AppWindow(1, "Inbox - Mail", "chrome.exe"),
        actions.AppWindow(2, "YouTube", "chrome.exe"),
        actions.AppWindow(3, "notes.txt", "notepad.exe"),
    ]
    minimized: list[int] = []
    count = actions.minimize_app("Chrome.exe", windows=lambda: windows, minimize=minimized.append)
    assert count == 2 and minimized == [1, 2]
    assert actions.minimize_app("", windows=lambda: windows, minimize=minimized.append) == 0


def test_hotkey_parsing() -> None:
    chord = hotkey.parse("ctrl+alt+m")
    assert chord is not None and chord.text == "Ctrl+Alt+M" and chord.virtual_key == ord("M")
    f12 = hotkey.parse("Shift+F12")
    assert f12 is not None and f12.virtual_key == 0x7B
    win = hotkey.parse("Meta+Ctrl+1")
    assert win is not None and win.text == "Ctrl+Meta+1"
    for bad in ("M", "Ctrl+", "Ctrl+Ctrl+M", "Ctrl+Space", "Ctrl+F25", ""):
        assert hotkey.parse(bad) is None
    assert hotkey.normalize("nonsense") == hotkey.DEFAULT_HOTKEY


class FakeBackend:
    def __init__(self, taken: set[str]) -> None:
        self.taken = taken
        self.active: str | None = None

    def register(self, chord: hotkey.Chord) -> bool:
        if chord.text in self.taken:
            return False
        self.active = chord.text
        return True

    def unregister(self) -> None:
        self.active = None


def test_hotkey_failure_keeps_previous_registration() -> None:
    backend = FakeBackend(taken={"Ctrl+Alt+T"})
    key = hotkey.GlobalHotkey(lambda: None, backend=backend)
    assert key.set("Ctrl+Alt+M") and backend.active == "Ctrl+Alt+M"
    assert not key.set("Ctrl+Alt+T")
    assert backend.active == "Ctrl+Alt+M" and key.current is not None
    assert not key.set("garbage")
    key.close()
    assert backend.active is None


def test_new_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    config.save(
        Settings(
            mirror_hotkey="alt+shift+f9",
            action="minimize_app",
            target_apps=(" LINE.exe ",),
        ).normalized(),
        path,
    )
    loaded = config.load(path)
    assert loaded.mirror_hotkey == "Alt+Shift+F9"
    assert loaded.action == "minimize_app" and loaded.target_apps == ("line.exe",)
