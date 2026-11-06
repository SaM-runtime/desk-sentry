"""Discreet mode hides noisy/identifying surfaces without changing alert decisions."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from desk_sentry import actions, config
from desk_sentry.app import DeskSentryApp
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.people import Level, PeopleBook
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from test_app_hide04 import app_with
from test_app_review05c import local_app as local_app

pytestmark = pytest.mark.usefixtures("qapp")


def discreet_controller(**changes):
    settings = Settings(discreet=True, **changes)
    instance = app_with(settings)
    instance.discreet_cue = Mock()
    instance.tray = Mock()
    instance.curtain = Mock(visible=False)
    instance._latest_snapshot = Snapshot(Status.PAUSED)
    return instance


def test_discreet_alert_keeps_action_but_never_shows_camera_or_noise(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    instance = discreet_controller(action="minimize_app", target_apps=("mail.exe",))
    minimize = Mock(return_value={10: True})
    sound = Mock()
    monkeypatch.setattr(actions, "minimize_apps", minimize)
    monkeypatch.setattr(actions, "play_alert_sound", sound)
    frame = np.full((24, 32, 3), 200, dtype=np.uint8)
    snapshot = Snapshot(
        Status.ALERT, frame=frame, alert_started=True, alert_level=Level.HIGH, evade_near=True
    )

    instance._on_snapshot(snapshot)

    shown = instance.window.show_snapshot.call_args.args[0]
    assert shown.frame is None and shown.tracks == () and shown.self_zone is None
    assert shown.status is Status.ALERT
    instance.mini.show.assert_not_called()
    instance.mini.set_snapshot.assert_not_called()
    instance.popup.update_snapshot.assert_not_called()
    instance.flash.flash.assert_not_called()
    sound.assert_not_called()
    instance.tray.showMessage.assert_not_called()
    assert instance.engine.preview_wanted is False
    instance.discreet_cue.update_status.assert_called_once_with(Status.ALERT)
    now[0] = 0.4
    instance._on_snapshot(replace(snapshot, alert_started=False))
    minimize.assert_called_once_with(("mail.exe",))
    instance.mini.show.assert_not_called()
    instance.popup.update_snapshot.assert_not_called()
    instance.flash.flash.assert_not_called()
    sound.assert_not_called()
    instance.tray.showMessage.assert_not_called()


def test_discreet_off_restores_existing_popup_flash_sound_and_action(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    instance = discreet_controller(action="lock")
    sound = Mock()
    execute = Mock()
    monkeypatch.setattr(actions, "play_alert_sound", sound)
    monkeypatch.setattr(actions, "run_action", execute)
    snapshot = Snapshot(Status.ALERT, alert_started=True, alert_level=Level.HIGH, evade_near=True)
    instance._on_snapshot(snapshot)
    sound.assert_not_called()
    instance.flash.flash.assert_not_called()
    now[0] = 0.4
    instance._on_snapshot(replace(snapshot, alert_started=False))
    instance.window.settings = replace(instance.window.settings, discreet=False)

    now[0] = 31.0
    instance._on_snapshot(snapshot)

    instance.popup.update_snapshot.assert_called_once_with(snapshot)
    instance.flash.flash.assert_called_once()
    sound.assert_called_once()
    now[0] = 31.4
    instance._on_snapshot(replace(snapshot, alert_started=False))
    assert execute.call_count == 2


@pytest.mark.parametrize("diagnostics", [False, True])
def test_discreet_suppresses_every_marker_and_hide_to_tray_notice(diagnostics):
    instance = discreet_controller(diagnostics=diagnostics)
    instance._last_status = Status.CLEAR
    instance._diagnostics = SimpleNamespace(write=Mock())
    instance._hint_shown = False

    instance.mark("false_alert")
    instance._hidden_to_tray()

    instance.tray.showMessage.assert_not_called()
    assert instance._diagnostics.write.call_count == int(diagnostics)


def test_discreet_mirror_hotkey_toggles_main_window_without_changing_compact_preference():
    instance = discreet_controller(compact_mode=True)
    visible = True
    instance.window.isVisible.side_effect = lambda: visible
    instance.toggle_mini()
    instance.window.hide.assert_called_once()
    visible = False
    instance.toggle_mini()
    instance.window.show.assert_called_once()
    instance.mini.show.assert_not_called()
    instance.window.update_settings.assert_not_called()


def test_discreet_curtain_keeps_mirror_hidden_and_plain():
    instance = discreet_controller(action="curtain")
    instance._show_curtain()
    instance.curtain.set_discreet.assert_called_once_with(True)
    instance.curtain.show.assert_called_once_with(instance.window.settings.hide_hotkey)
    instance.mini.show.assert_not_called()
    instance.mini.set_snapshot.assert_not_called()
    assert instance.engine.preview_wanted is False


def test_discreet_tray_keeps_app_icon_stable_across_statuses(qapp):
    instance = discreet_controller()
    instance._arm_action = Mock()
    captures = []
    instance.tray.setIcon.side_effect = lambda icon: captures.append(icon.pixmap(24, 24).toImage())
    DeskSentryApp._render_tray(instance, instance.window.settings, Status.ALERT)
    DeskSentryApp._render_tray(instance, instance.window.settings, Status.APPROACHING)
    assert captures[0] == captures[1]
    assert captures[0] == theme.app_icon().pixmap(24, 24).toImage()
    instance.tray.setToolTip.assert_called_with("Desk Sentry（低調模式）")
    instance.window.settings.discreet = False
    DeskSentryApp._render_tray(instance, instance.window.settings, Status.CLEAR)
    assert captures[-1] != captures[0]
    assert instance.tray.setToolTip.call_args.args[0].startswith("Desk Sentry")


def test_discreet_startup_stays_in_tray_and_detection_still_starts(local_app):
    build, path, started = local_app
    config.save(Settings(discreet=True, compact_mode=True), path)
    instance = build(start_hidden=False)
    assert instance.tray.shown
    assert started == [instance.engine]
    assert not instance.window.isVisible() and not instance.mini.isVisible()
    assert not instance.popup.isVisible()
    assert instance.engine.preview_wanted is False
    assert instance.window.windowTitle() == "Desk Sentry 後照鏡"
    assert instance.engine._settings.armed


def test_discreet_suppresses_startup_people_load_error_notification(local_app, monkeypatch):
    build, path, _started = local_app
    config.save(Settings(discreet=True), path)

    def failed_load(people):
        people.load_error = "injected invalid people store"

    monkeypatch.setattr(PeopleBook, "load", failed_load)
    instance = build(start_hidden=False)
    assert instance.people.load_error
    assert instance.tray.messages == []
    assert instance.tray.shown and not instance.window.isVisible()


def test_switching_discreet_clears_visible_surfaces_and_restores_previous_mirror(
    local_app, monkeypatch
):
    build, _path, _started = local_app
    instance = build()
    stop = Mock()
    monkeypatch.setattr(actions, "stop_alert_sound", stop)
    instance.mini.show()
    frame = np.full((24, 32, 3), 125, dtype=np.uint8)
    instance._on_snapshot(Snapshot(Status.CLEAR, frame=frame))
    instance.popup.update_snapshot(Snapshot(Status.ALERT, frame=frame))
    instance.flash.flash(theme.STATUS_COLOR[Status.ALERT])
    assert instance.mini.isVisible() and instance.popup.isVisible()

    instance.window.update_settings(discreet=True, discreet_cue="none")

    assert not instance.mini.isVisible() and not instance.popup.isVisible()
    assert all(
        not flash.isVisible() and not flash._timer.isActive() for flash in instance.flash._flashes
    )
    assert instance.window.settings.show_mirror and instance.window.settings.edge_flash
    assert instance.window.settings.sound and not instance.window.settings.compact_mode
    assert instance.engine._settings.armed
    assert instance.engine.preview_wanted is False
    stop.assert_called_once()
    instance.window.update_settings(discreet=False)
    assert instance.mini.isVisible()
    assert not instance.window.settings.compact_mode


def test_discreet_history_title_masks_and_restores_on_settings_switch(local_app, monkeypatch):
    build, _path, _started = local_app
    instance = build()
    monkeypatch.setattr(actions, "stop_alert_sound", lambda: None)
    instance.history.add(np.full((16, 24, 3), 100, dtype=np.uint8))
    instance.open_history(None)
    assert instance._history_window.windowTitle() == "Desk Sentry 最近提醒"
    instance.window.update_settings(discreet=True, discreet_cue="none")
    assert instance._history_window.windowTitle() == "紀錄"
    assert instance._history_window._discreet
    instance.window.update_settings(discreet=False)
    assert instance._history_window.windowTitle() == "Desk Sentry 最近提醒"
    instance._history_window.hide()
    instance._history_window.deleteLater()


def test_audio_stop_error_cannot_leave_camera_surfaces_visible_on_discreet_entry(
    local_app, monkeypatch, caplog
):
    build, _path, _started = local_app
    instance = build()
    instance.mini.show()
    instance.popup.show()

    def fail_stop():
        raise RuntimeError("injected audio driver failure")

    monkeypatch.setattr(actions, "stop_alert_sound", fail_stop)
    instance.window.update_settings(discreet=True, discreet_cue="none")
    assert not instance.mini.isVisible() and not instance.popup.isVisible()
    assert instance.engine.preview_wanted is False
    assert instance.window.settings.discreet and instance.engine._settings.armed
    assert "injected audio driver failure" in caplog.text
