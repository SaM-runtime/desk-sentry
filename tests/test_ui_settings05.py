"""Task 05 settings controls and automatic pause presentation."""

from PySide6.QtCore import QTime
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow


def _window(qtbot: QtBot, settings: Settings, seen: list[Settings]) -> MainWindow:
    window = MainWindow(settings, seen.append)
    qtbot.addWidget(window)
    window.show()
    return window


def test_task05_settings_render_and_report_changes(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(), seen)

    assert window.action.findData("curtain") >= 0
    window.away_action.setCurrentIndex(window.away_action.findData("minimize_app"))
    window.away_minutes.setValue(12)
    window.hide_hotkey_style.setCurrentIndex(window.hide_hotkey_style.findData("curtain"))
    window.pause_when_locked.setChecked(False)
    window.schedule_enabled.setChecked(True)
    window.schedule_start.setTime(QTime(21, 30))
    window.schedule_end.setTime(QTime(6, 15))
    window.schedule_day_buttons[5].click()

    assert seen[-1].schedule_days == (0, 1, 2, 3, 4, 5)
    assert window._settings.away_action == "minimize_app"
    assert window._settings.away_minutes == 12
    assert window._settings.hide_hotkey_style == "curtain"
    assert window._settings.pause_when_locked is False
    assert window._settings.schedule_enabled is True
    assert window._settings.schedule_start == "21:30"
    assert window._settings.schedule_end == "06:15"


def test_schedule_dependency_and_weekday_selection(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(schedule_enabled=False, schedule_days=(1, 3, 5)),
        [],
    )

    assert not window.schedule_fields_row.isEnabled()
    assert not window.schedule_days_row.isEnabled()
    assert [button.isChecked() for button in window.schedule_day_buttons] == [
        False,
        True,
        False,
        True,
        False,
        True,
        False,
    ]
    window.schedule_enabled.setChecked(True)
    assert window.schedule_fields_row.isEnabled()
    assert window.schedule_days_row.isEnabled()


def test_target_apps_remain_available_for_away_minimize(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(
            action="none",
            hide_hotkey="",
            hide_hotkey_style="curtain",
            away_action="minimize_app",
        ),
        [],
    )
    assert window.target_app_row.isVisibleTo(window)

    window.away_action.setCurrentIndex(window.away_action.findData("none"))
    assert not window.target_app_row.isVisibleTo(window)
    assert window.hide_hotkey_hint.text() == "按一下蓋上黑幕，再按一下移開"


def test_pause_reason_has_visible_stage_copy(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(), [])

    window.set_pause_reason("locked")
    assert window.pause_reason.isVisibleTo(window)
    assert window.pause_reason.text() == "已暫停 · 電腦鎖定中"

    window.set_pause_reason("schedule")
    assert window.pause_reason.text() == "已暫停 · 非監看時段"

    window.set_pause_reason("manual")
    assert not window.pause_reason.isVisible()
