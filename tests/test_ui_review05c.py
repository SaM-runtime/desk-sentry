"""Task 05c automatic-pause presentation regressions."""

from datetime import datetime

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.sentry import Status
from desk_sentry.ui.main_window import MainWindow


def _window(qtbot: QtBot, settings: Settings, seen: list[Settings]) -> MainWindow:
    window = MainWindow(settings, seen.append)
    qtbot.addWidget(window)
    window.resize(1180, 800)
    window.show()
    qtbot.waitExposed(window)
    window.show_snapshot(Snapshot(Status.PAUSED))
    return window


def test_schedule_pause_hides_resume_and_does_not_change_user_armed(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(
        qtbot,
        Settings(
            armed=True,
            schedule_enabled=True,
            schedule_start="08:30",
            schedule_end="18:30",
            schedule_days=(0, 1, 2, 3, 4),
        ),
        seen,
    )

    window.set_pause_reason("schedule", now=datetime(2026, 10, 12, 7, 45))
    window.mirror.grab()

    assert window.mirror.pause_detail == "非監看時段 · 08:30 自動開始"
    assert window.mirror.resume_rect.isEmpty()
    window.mirror.resume_requested.emit()
    assert window.settings.armed is True
    assert seen == []


def test_schedule_pause_names_the_next_start_weekday(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(
            schedule_enabled=True,
            schedule_start="08:30",
            schedule_end="18:30",
            schedule_days=(2,),
        ),
        [],
    )

    window.set_pause_reason("schedule", now=datetime(2026, 10, 12, 19, 0))

    assert window.mirror.pause_detail == "非監看時段 · 週三 08:30 自動開始"


def test_automatic_pause_copy_has_honest_fallback_and_locked_reason(qtbot: QtBot) -> None:
    window = _window(
        qtbot,
        Settings(
            schedule_enabled=True,
            schedule_start="08:30",
            schedule_end="08:30",
            schedule_days=(),
        ),
        [],
    )

    window.set_pause_reason("schedule", now=datetime(2026, 10, 12, 7, 45))
    assert window.mirror.pause_detail == "非監看時段"

    window.set_pause_reason("locked")
    assert window.mirror.pause_detail == "電腦鎖定中"


def test_manual_pause_keeps_resume_button_and_behavior(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(armed=False), seen)

    window.set_pause_reason("manual")
    window.mirror.grab()
    resume_rect = window.mirror.resume_rect

    assert not resume_rect.isEmpty()
    qtbot.mouseClick(
        window.mirror,
        Qt.MouseButton.LeftButton,
        pos=resume_rect.center().toPoint(),
    )
    assert window.settings.armed is True
    assert seen[-1].armed is True
