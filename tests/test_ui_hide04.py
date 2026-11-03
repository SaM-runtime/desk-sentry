"""Task 04 UI behavior for multi-program targets and the hide hotkey."""

from PySide6.QtGui import QKeySequence
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow

APPS = [
    ("chrome.exe", "Inbox"),
    ("chrome.exe", "YouTube"),
    ("line.exe", "LINE"),
    ("outlook.exe", "Outlook"),
    ("teams.exe", "Teams"),
    ("code.exe", "Visual Studio Code"),
    ("notepad.exe", "Notes"),
    ("calc.exe", "Calculator"),
    ("paint.exe", "Paint"),
]


def _window(qtbot: QtBot, settings: Settings, seen: list[Settings]) -> MainWindow:
    window = MainWindow(settings, seen.append, list_apps=lambda: APPS)
    qtbot.addWidget(window)
    window.show()
    return window


def test_target_app_chips_render_zero_three_and_eight(qtbot: QtBot) -> None:
    eight_unique = tuple(dict.fromkeys(exe for exe, _title in APPS))
    for targets in ((), ("chrome.exe", "line.exe", "teams.exe"), eight_unique):
        window = _window(qtbot, Settings(target_apps=targets), [])
        assert [chip.exe for chip in window.target_app_chips] == list(targets)
        assert window.target_app.itemText(0) == "＋ 加入程式…"
        assert window.target_app.isEnabled() is (len(targets) < 8)
        assert window.target_app.toolTip() == ("最多 8 個程式" if len(targets) == 8 else "")
        window.close()


def test_target_app_picker_adds_and_chip_remove_writes_settings(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(target_apps=("chrome.exe",)), seen)

    line_index = window.target_app.findData("line.exe")
    window.target_app.setCurrentIndex(line_index)
    window.target_app.activated.emit(line_index)
    assert seen[-1].target_apps == ("chrome.exe", "line.exe")
    assert window.target_app.currentIndex() == 0
    assert [chip.exe for chip in window.target_app_chips] == ["chrome.exe", "line.exe"]

    window.target_app_chips[0].remove_button.click()
    assert seen[-1].target_apps == ("line.exe",)
    assert [chip.exe for chip in window.target_app_chips] == ["line.exe"]


def test_target_rows_follow_action_and_hide_hotkey(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(action="none", hide_hotkey="Ctrl+Alt+H"), [])
    assert window.target_app_row.isVisibleTo(window)
    assert not window.restore_after_alert_row.isVisibleTo(window)

    window.hide_hotkey.setKeySequence(QKeySequence())
    window.hide_hotkey.editingFinished.emit()
    assert not window.target_app_row.isVisibleTo(window)

    window.action.setCurrentIndex(window.action.findData("minimize_app"))
    assert window.target_app_row.isVisibleTo(window)
    assert window.restore_after_alert_row.isVisibleTo(window)


def test_restore_after_alert_switch_reports_setting(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(action="minimize_app"), seen)
    window.restore_after_alert.setChecked(True)
    assert seen[-1].restore_after_alert is True


def test_hide_hotkey_rejects_mirror_conflict_and_backend_failure(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(), seen)
    assert window.hide_hotkey_hint.text() == "按一下縮小目標程式，再按一下還原"

    window.hide_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+M"))
    window.hide_hotkey.editingFinished.emit()
    assert window.hide_hotkey.keySequence().toString() == "Ctrl+Alt+H"
    assert window.hide_hotkey_hint.text() == "與小鏡子快捷鍵相同"
    assert not seen

    window.on_hide_hotkey = lambda _text: False
    window.hide_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+K"))
    window.hide_hotkey.editingFinished.emit()
    assert window.hide_hotkey.keySequence().toString() == "Ctrl+Alt+H"
    assert "佔用" in window.hide_hotkey_hint.text()
    assert not seen


def test_mirror_hotkey_rejects_hide_conflict_on_hide_hint(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(), seen)
    window.hotkey.setKeySequence(QKeySequence("Ctrl+Alt+H"))
    window.hotkey.editingFinished.emit()
    assert window.hotkey.keySequence().toString() == "Ctrl+Alt+M"
    assert window.hide_hotkey_hint.text() == "與小鏡子快捷鍵相同"
    assert not seen
