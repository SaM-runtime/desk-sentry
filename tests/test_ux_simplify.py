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


def test_every_privacy_switch_starts_off() -> None:
    s = Settings()
    assert not s.away_guard
    assert not s.away_guard_save
    assert not s.away_guard_when_locked
    assert not s.diagnostics
    assert not s.update_check_enabled


def test_away_guard_options_appear_only_once_the_guard_is_on(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(), seen)
    rows = (window.away_guard_save_row, window.away_guard_locked_row)
    assert not window.away_guard.isChecked()
    assert all(not row.isVisibleTo(window) for row in rows)
    assert not window.away_guard_keep_row.isVisibleTo(window)

    window.away_guard.click()
    assert seen[-1].away_guard is True
    assert all(row.isVisibleTo(window) for row in rows)
    assert not window.away_guard_save.isChecked()  # saving is still opt-in
    assert not window.away_guard_keep_row.isVisibleTo(window)

    window.away_guard_save.click()
    assert seen[-1].away_guard_save is True
    assert window.away_guard_keep_row.isVisibleTo(window)
    window.away_guard_keep_days.setCurrentIndex(window.away_guard_keep_days.findData("30"))
    assert seen[-1].away_guard_keep_days == 30
    assert "加密存在本機" in window.privacy_label.text()  # the footer never overstates privacy


def test_watching_while_locked_is_its_own_switch(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(qtbot, Settings(away_guard=True), seen)
    assert window.pause_when_locked_notice.isHidden()

    window.away_guard_when_locked.click()
    assert seen[-1].away_guard_when_locked is True
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


def test_only_the_locked_switch_keeps_the_guard_watching_while_locked() -> None:
    from test_app_automation05 import EngineRecorder, TimerRecorder

    cases = (((False, False), "locked"), ((True, False), "locked"), ((True, True), None))
    for (guard, when_locked), expected in cases:
        app = app_with(
            Settings(pause_when_locked=True, away_guard=guard, away_guard_when_locked=when_locked)
        )
        app.engine = EngineRecorder()
        app._schedule_timer = TimerRecorder()
        app._render_tray = Mock()
        app._refresh_camera_presentation = Mock()
        app.discreet_cue = Mock()
        app._locked = True
        app._apply_monitoring()
        assert app._pause_reason == expected
