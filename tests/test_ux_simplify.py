"""Usability fixes from tester feedback: fewer visible settings, findable exits."""

from unittest.mock import Mock

import pytest
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with

pytestmark = pytest.mark.usefixtures("qapp")


def _window(qtbot: QtBot, settings: Settings, seen: list[Settings]) -> MainWindow:
    window = MainWindow(settings, seen.append)
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    return window


def test_advanced_settings_start_collapsed_and_expand_on_request(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(), [])
    # Everyday choices are on screen without expanding anything.
    for control in (window.sensitivity, window.show_mirror, window.away_guard, window.camera):
        assert control.isVisibleTo(window)
    # Rarely used ones wait behind 「進階設定」.
    for control in (window.discreet, window.action, window.hide_hotkey, window.schedule_enabled):
        assert not control.isVisibleTo(window)
    assert window.advanced_toggle.text().endswith("顯示進階設定")

    window.advanced_toggle.click()
    assert window.discreet.isVisibleTo(window)
    assert window.schedule_enabled.isVisibleTo(window)
    assert window.advanced_toggle.text().endswith("隱藏進階設定")
    assert not window.advanced_hint.isVisibleTo(window)


def test_away_guard_switch_saves_and_explains_the_lock_interaction(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(), seen)
    assert not window.away_guard.isChecked()
    assert window.pause_when_locked_notice.isHidden()

    window.away_guard.click()
    assert seen[-1].away_guard is True
    assert not window.pause_when_locked_notice.isHidden()
    assert window.pause_when_locked_row.graphicsEffect() is not None  # shown as not applicable


def test_unseen_visits_show_a_banner_that_opens_the_photos(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(away_guard=True), [])
    opened = []
    window.on_open_visits = lambda: opened.append(True)
    assert not window.visits_banner.isVisible()

    window.set_unseen_visits(2)
    assert window.visits_banner.isVisible()
    assert "2 次有人靠近" in window.visits_banner.text()
    assert window.open_visits_button.text() == "查看離座紀錄（2 則新）"
    window.visits_banner.click()
    assert opened == [True]

    window.set_unseen_visits(0)
    assert not window.visits_banner.isVisible()


def test_discreet_stage_has_a_visible_way_back(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(discreet=True), seen)
    assert window.leave_discreet_button.isVisible()
    window.leave_discreet_button.click()
    assert seen[-1].discreet is False
    assert not window.leave_discreet_button.isVisible()


def test_tray_can_leave_discreet_mode_without_finding_the_setting() -> None:
    app = app_with(Settings(discreet=True))
    app.show_current = Mock()
    app.leave_discreet()
    app.window.update_settings.assert_called_once_with(discreet=False)
    app.show_current.assert_called_once()


def test_away_guard_keeps_watching_while_the_computer_is_locked() -> None:
    from test_app_automation05 import EngineRecorder, TimerRecorder

    for guard, expected in ((False, "locked"), (True, None)):
        app = app_with(Settings(pause_when_locked=True, away_guard=guard))
        app.engine = EngineRecorder()
        app._schedule_timer = TimerRecorder()
        app._render_tray = Mock()
        app._refresh_camera_presentation = Mock()
        app.discreet_cue = Mock()
        app._locked = True
        app._apply_monitoring()
        assert app._pause_reason == expected
