import numpy as np
from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.ui.main_window import MainWindow
from desk_sentry.ui.overlays import MirrorPopup


def _alert_snapshot(started: bool = True) -> Snapshot:
    return Snapshot(
        status=Status.ALERT,
        frame=np.zeros((540, 960, 3), np.uint8),
        tracks=(
            TrackView(1, (0.3, 0.3, 0.7, 1.0), Role.SELF, 0.9),
            TrackView(2, (0.05, 0.2, 0.15, 0.6), Role.INTRUDER, 0.8),
        ),
        self_zone=(0.25, 0.25, 0.75, 1.0),
        intruders=1,
        alert_started=started,
    )


def test_settings_changes_are_reported(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = MainWindow(Settings(), seen.append)
    qtbot.addWidget(window)
    window.sound.setChecked(False)
    window.sensitivity.setCurrentIndex(window.sensitivity.findData("high"))
    window.recalibrate.click()
    assert seen[-1].sound is False
    assert seen[-1].sensitivity == "high"
    assert seen[-1].self_zone is None


def test_arm_toggle(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = MainWindow(Settings(armed=True), seen.append)
    qtbot.addWidget(window)
    window.arm_button.click()
    assert seen[-1].armed is False
    assert window.arm_button.text() == "開始監看"


def test_window_renders_alert(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda s: None)
    qtbot.addWidget(window)
    window.show()
    window.show_snapshot(_alert_snapshot())
    window.mirror.grab()  # exercises paintEvent with overlays
    assert window._status_title.text() == "後方有人"


def test_popup_shows_on_alert_and_lingers(qtbot: QtBot) -> None:
    popup = MirrorPopup()
    qtbot.addWidget(popup)
    popup.update_snapshot(_alert_snapshot())
    assert popup.isVisible()
    popup.update_snapshot(Snapshot(Status.CLEAR))
    assert popup.isVisible()  # lingers briefly
    qtbot.waitUntil(lambda: not popup.isVisible(), timeout=4000)


def test_popup_dismissed_stays_hidden_until_next_alert(qtbot: QtBot) -> None:
    popup = MirrorPopup()
    qtbot.addWidget(popup)
    popup.update_snapshot(_alert_snapshot())
    qtbot.mouseClick(popup, Qt.MouseButton.LeftButton)
    popup.update_snapshot(_alert_snapshot(started=False))
    assert not popup.isVisible()
    popup.update_snapshot(_alert_snapshot(started=True))
    assert popup.isVisible()


def test_clicking_a_person_sets_self_zone(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = MainWindow(Settings(exclude_self=False), seen.append)
    qtbot.addWidget(window)
    window.resize(1040, 640)
    window.show()
    window.show_snapshot(_alert_snapshot())
    # Intruder box is at x 0.05–0.15 in camera coords -> right side of the mirrored view.
    target = window.mirror._image_rect()
    assert target is not None
    x = target.left() + (1 - 0.10) * target.width()
    y = target.top() + 0.4 * target.height()
    qtbot.mouseClick(window.mirror, Qt.MouseButton.LeftButton, pos=QPoint(round(x), round(y)))
    assert seen[-1].exclude_self is True
    assert seen[-1].self_zone is not None and seen[-1].self_zone[2] < 0.3


def test_autostart_checkbox_reflects_registry_result(qtbot: QtBot) -> None:
    registry = {"enabled": False, "writable": True}

    def get() -> bool:
        return registry["enabled"]

    def set_(enabled: bool) -> bool:
        if registry["writable"]:
            registry["enabled"] = enabled
        return registry["writable"]

    window = MainWindow(Settings(), lambda s: None, autostart=(get, set_))
    qtbot.addWidget(window)
    assert not window.autostart.isChecked()
    window.autostart.setChecked(True)
    assert registry["enabled"] is True
    registry["writable"] = False  # e.g. blocked by policy
    window.autostart.setChecked(False)
    assert window.autostart.isChecked()  # snaps back to the real state


def test_diagnostics_checkbox(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    opened: list[bool] = []
    window = MainWindow(Settings(), seen.append)
    window.on_open_diagnostics = lambda: opened.append(True)
    qtbot.addWidget(window)
    window.diagnostics.setChecked(True)
    window.open_diagnostics.click()
    assert seen[-1].diagnostics is True and opened == [True]


def test_compact_button_calls_back(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda s: None)
    qtbot.addWidget(window)
    called: list[bool] = []
    window.on_compact = lambda: called.append(True)
    window.compact_button.click()
    assert called == [True]


def test_minimize_app_picker_lists_programs_once(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    apps = [("chrome.exe", "Inbox"), ("chrome.exe", "YouTube"), ("line.exe", "LINE")]
    window = MainWindow(Settings(hide_hotkey=""), seen.append, list_apps=lambda: apps)
    qtbot.addWidget(window)
    assert not window.target_app_row.isVisibleTo(window)
    window.action.setCurrentIndex(window.action.findData("minimize_app"))
    assert window.target_app_row.isVisibleTo(window)
    assert window.target_app.count() == 3  # placeholder + 2 programs
    window.target_app.setCurrentIndex(window.target_app.findData("line.exe"))
    window.target_app.activated.emit(window.target_app.currentIndex())
    assert seen[-1].action == "minimize_app" and seen[-1].target_apps == ("line.exe",)


def test_hotkey_field_reverts_on_failure(qtbot: QtBot) -> None:
    from PySide6.QtGui import QKeySequence

    seen: list[Settings] = []
    window = MainWindow(Settings(), seen.append)
    qtbot.addWidget(window)
    window.on_hotkey = lambda text: text != "Ctrl+Alt+T"
    window.hotkey.setKeySequence(QKeySequence("Ctrl+Alt+T"))
    window.hotkey.editingFinished.emit()
    assert window.hotkey.keySequence().toString() == "Ctrl+Alt+M"
    assert "佔用" in window.hotkey_hint.text()
    window.hotkey.setKeySequence(QKeySequence("Ctrl+Alt+K"))
    window.hotkey.editingFinished.emit()
    assert seen[-1].mirror_hotkey == "Ctrl+Alt+K"
