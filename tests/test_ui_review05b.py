"""Task 05b UI review regressions."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractSpinBox, QDateTimeEdit
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def _window(qtbot: QtBot, settings: Settings) -> MainWindow:
    window = MainWindow(settings, lambda _settings: None)
    qtbot.addWidget(window)
    window.show()
    return window


def test_alert_action_change_keeps_targets_visible_for_away_minimize(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(action="minimize_app", away_action="minimize_app", hide_hotkey=""),
    )

    window.action.setCurrentIndex(window.action.findData("none"))

    assert window.target_app_row.isVisibleTo(window)
    assert not window.restore_after_alert_row.isVisibleTo(window)


def test_schedule_time_fields_hide_buttons_and_keep_keyboard_editing(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(schedule_enabled=True, schedule_start="08:00", schedule_end="18:00"),
    )

    for editor in (window.schedule_start, window.schedule_end):
        assert editor.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
        assert editor.alignment() == Qt.AlignmentFlag.AlignCenter
        assert editor.size().width() == theme.D["schedule_time_width"]
        assert editor.size().height() == theme.D["control_height"]

    window.schedule_start.setCurrentSection(QDateTimeEdit.Section.HourSection)
    window.schedule_start.setFocus()
    qtbot.keyClick(window.schedule_start, Qt.Key.Key_Up)

    assert window.schedule_start.time().toString("HH:mm") == "09:00"
    assert window.settings.schedule_start == "09:00"
