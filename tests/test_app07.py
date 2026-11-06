"""TASK-07 behavior at the app callback boundary, with every native side effect injected."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desk_sentry import actions
from desk_sentry.app import DeskSentryApp
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.health import HealthState
from desk_sentry.people import Level
from desk_sentry.sentry import Role, Status
from desk_sentry.ui import theme
from test_app_hide04 import app_with
from test_app_review05c import local_app as local_app

pytestmark = pytest.mark.usefixtures("qapp")


def alert(**changes):
    return Snapshot(
        Status.ALERT,
        alert_level=Level.HIGH,
        tracks=(TrackView(1, (0.2, 0.1, 0.5, 0.6), Role.INTRUDER, 0.9),),
        **changes,
    )


def test_immediate_alert_presentation_but_short_alert_never_runs_action(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    execute, sound = Mock(), Mock()
    monkeypatch.setattr(actions, "run_action", execute)
    monkeypatch.setattr(actions, "play_alert_sound", sound)
    controller = app_with(Settings(action="lock"))
    controller._on_snapshot(alert(alert_started=True))
    controller.popup.update_snapshot.assert_called_once()
    controller.flash.flash.assert_called_once()
    sound.assert_called_once()
    execute.assert_not_called()
    now[0] = 0.399
    controller._on_snapshot(alert())
    execute.assert_not_called()
    now[0] = 0.4
    controller._on_snapshot(Snapshot(Status.CLEAR))
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("role", "height", "level", "expected"),
    [
        (Role.INTRUDER, 0.344, Level.HIGH, False),
        (Role.INTRUDER, 0.4, Level.MEDIUM, False),
        (Role.APPROACHING, 0.1, Level.HIGH, True),
    ],
)
def test_confirmed_action_respects_near_evidence_and_existing_high_permission(
    monkeypatch, role, height, level, expected
):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    execute = Mock()
    monkeypatch.setattr(actions, "run_action", execute)
    controller = app_with(Settings(action="lock", sound=False))
    track = TrackView(1, (0.2, 0.1, 0.5, 0.1 + height), role, 0.9)
    controller._on_snapshot(
        Snapshot(Status.ALERT, tracks=(track,), alert_started=True, alert_level=level)
    )
    now[0] = 0.4
    controller._on_snapshot(Snapshot(Status.ALERT, tracks=(track,), alert_level=level))
    assert execute.call_count == int(expected)


def test_manual_hotkey_is_immediate_even_during_automatic_cooldown(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    execute, manual = Mock(), Mock(return_value={20: False})
    monkeypatch.setattr(actions, "run_action", execute)
    controller = app_with(Settings(action="lock", sound=False))
    controller._hide_toggle = actions.HideToggle(minimize_selected=manual, minimize_all=manual)
    controller._on_snapshot(alert(alert_started=True))
    now[0] = 0.4
    controller._on_snapshot(alert())
    execute.assert_called_once_with("lock")
    now[0] = 0.401
    controller.toggle_hidden_windows()
    manual.assert_called_once()


@pytest.mark.parametrize("action", ["none", "minimize", "minimize_app", "lock", "curtain"])
def test_cover_precedes_every_confirmed_automatic_action_and_cooldown(monkeypatch, action):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    events = []
    monkeypatch.setattr(
        actions,
        "bring_cover",
        lambda exe: events.append(("cover", exe)) or actions.CoverResult(True, True, 99, 10),
    )
    monkeypatch.setattr(actions, "run_action", lambda mode: events.append(("action", mode)))
    monkeypatch.setattr(
        actions,
        "minimize_apps",
        lambda exes: events.append(("action", "minimize_app")) or {20: False},
    )
    controller = app_with(Settings(action=action, cover_app="cover.exe", sound=False))
    controller._show_curtain = lambda: events.append(("action", "curtain"))
    controller._on_snapshot(alert(alert_started=True))
    assert events == []
    now[0] = 0.4
    controller._on_snapshot(alert())
    assert events == [("cover", "cover.exe")] + ([] if action == "none" else [("action", action)])
    original = list(events)
    now[0] = 1.0
    controller._on_snapshot(Snapshot(Status.CLEAR))
    now[0] = 2.0
    controller._on_snapshot(alert(alert_started=True))
    now[0] = 2.4
    controller._on_snapshot(alert())
    assert events == original


def test_missing_cover_is_logged_once_and_window_action_still_runs(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    monkeypatch.setattr(actions, "bring_cover", lambda _exe: actions.CoverResult())
    execute = Mock()
    monkeypatch.setattr(actions, "run_action", execute)
    controller = app_with(
        Settings(action="lock", cover_app="missing.exe", diagnostics=True, sound=False)
    )
    controller._diagnostics = SimpleNamespace(write=Mock())
    for start in [0.0, 31.0]:
        now[0] = start
        controller._on_snapshot(alert(alert_started=True))
        now[0] = start + 0.4
        controller._on_snapshot(alert())
        controller._on_snapshot(Snapshot(Status.CLEAR))
    assert execute.call_count == 2
    assert [call.args[0] for call in controller._diagnostics.write.call_args_list] == [
        "cover_missing"
    ]


def test_cover_restores_previous_foreground_after_quiet_period(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        actions, "bring_cover", lambda _exe: actions.CoverResult(True, True, 99, 10)
    )
    restore = Mock()
    monkeypatch.setattr(actions, "restore_foreground", restore)
    controller = app_with(Settings(cover_app="cover.exe", restore_after_alert=True, sound=False))
    controller._on_snapshot(alert(alert_started=True))
    now[0] = 0.4
    controller._on_snapshot(alert())
    now[0] = 1.0
    controller._on_snapshot(Snapshot(Status.WATCHING))
    now[0] = 5.999
    controller._on_snapshot(Snapshot(Status.CLEAR))
    restore.assert_not_called()
    now[0] = 6.0
    controller._on_snapshot(Snapshot(Status.CLEAR))
    restore.assert_called_once_with(99)


def test_manual_hotkey_cover_is_immediate_and_second_press_restores(monkeypatch):
    events = []
    monkeypatch.setattr(
        actions,
        "bring_cover",
        lambda exe: events.append(("cover", exe)) or actions.CoverResult(True, True, 99, 10),
    )
    monkeypatch.setattr(
        actions, "restore_foreground", lambda hwnd: events.append(("foreground", hwnd))
    )
    controller = app_with(
        Settings(armed=False, cover_app="cover.exe", target_apps=("private.exe",))
    )
    controller._hide_toggle = actions.HideToggle(
        minimize_selected=lambda _exes: events.append(("minimize", 20)) or {20: False},
        restore=lambda _hwnds: events.append(("restore", 20)) or 1,
    )
    controller.toggle_hidden_windows()
    controller.toggle_hidden_windows()
    assert events == [("cover", "cover.exe"), ("minimize", 20), ("restore", 20), ("foreground", 99)]


@pytest.mark.parametrize("discreet", [False, True])
def test_feedback_records_alert_link_only_when_diagnostics_enabled_and_stays_quiet(discreet):
    controller = app_with(Settings(discreet=discreet, diagnostics=True))
    controller._diagnostics = SimpleNamespace(write=Mock())
    controller.discreet_cue = Mock()
    controller._last_alert_ts = 100.0
    controller._wall_now = lambda: datetime.fromtimestamp(102.0, UTC)
    controller.mark("false_alarm")
    fields = controller._diagnostics.write.call_args.kwargs
    assert (
        fields["marker"] == "false_alarm" and fields["alert_ts"] == 100.0 and fields["age_s"] == 2.0
    )
    assert controller.discreet_cue.pulse_feedback.call_count == int(discreet)
    controller._diagnostics.write.reset_mock()
    controller.window.settings.diagnostics = False
    controller.mark("real")
    controller._diagnostics.write.assert_not_called()


def test_feedback_without_alert_has_null_link():
    controller = app_with(Settings(diagnostics=True))
    controller._diagnostics = SimpleNamespace(write=Mock())
    controller.mark("real")
    fields = controller._diagnostics.write.call_args.kwargs
    assert fields["alert_ts"] is None and fields["age_s"] is None


def test_notice_in_normal_mode_never_uses_discreet_cue_or_alert_effects(monkeypatch):
    sound = Mock()
    monkeypatch.setattr(actions, "play_alert_sound", sound)
    controller = app_with(Settings(discreet=False))
    controller.discreet_cue = Mock()
    controller._on_snapshot(Snapshot(Status.NOTICE))
    assert controller.window.show_snapshot.call_args.args[0].status is Status.WATCHING
    controller.discreet_cue.update_status.assert_not_called()
    controller.flash.flash.assert_not_called()
    sound.assert_not_called()


def test_health_changes_redraw_tray_even_when_detection_status_stays_clear():
    controller = app_with(Settings())
    controller._on_snapshot(Snapshot(Status.CLEAR))
    controller._render_tray.reset_mock()
    controller._on_snapshot(Snapshot(Status.CLEAR, health_state=HealthState.DEGRADED))
    controller._on_snapshot(Snapshot(Status.CLEAR, health_state=HealthState.OK))
    assert controller._render_tray.call_count == 2


def test_opt_in_native_marker_registration_routes_to_linked_numeric_record(local_app, monkeypatch):
    import ctypes

    from desk_sentry import config, hotkey

    build, path, _started = local_app
    config.save(
        Settings(diagnostics=True, mark_false_hotkey="Ctrl+Alt+F", mark_real_hotkey="Ctrl+Alt+R"),
        path,
    )
    controller = build()
    writer = Mock()
    monkeypatch.setattr(controller._diagnostics, "write", writer)
    controller._last_alert_ts = 1_700_000_000.0
    controller._wall_now = lambda: datetime.fromtimestamp(1_700_000_003.0, UTC)
    try:
        for offset, key, kind in [
            (1, controller.mark_false_hotkey, "false_alarm"),
            (2, controller.mark_real_hotkey, "real"),
        ]:
            packet = hotkey._Message()
            packet.message = 0x0312
            packet.w_param = hotkey.HIDE_HOTKEY_ID + offset
            assert key._filter.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(packet))[
                0
            ]
            assert writer.call_args.kwargs["marker"] == kind
            assert writer.call_args.kwargs["alert_ts"] == 1_700_000_000.0
            assert writer.call_args.kwargs["age_s"] == 3.0
        assert controller.tray.messages == []
    finally:
        controller.mark_false_hotkey.close()
        controller.mark_real_hotkey.close()


@pytest.mark.parametrize("discreet", [False, True])
def test_health_tooltip_and_icon_degrade_and_recover_without_notifications(discreet):
    controller = app_with(Settings(discreet=discreet))
    controller.tray = Mock()
    controller._arm_action = Mock()
    controller._health_state = HealthState.DEGRADED
    DeskSentryApp._render_tray(controller, controller.window.settings, Status.ALERT)
    assert controller.tray.setToolTip.call_args.args[0] == (
        "Desk Sentry（低調模式・偵測中斷）" if discreet else "Desk Sentry · 後方有人（偵測中斷）"
    )
    expected = theme.app_icon() if discreet else theme.app_icon(theme.STATUS_COLOR[Status.PAUSED])
    assert (
        controller.tray.setIcon.call_args.args[0].pixmap(24, 24).toImage()
        == expected.pixmap(24, 24).toImage()
    )
    controller._health_state = HealthState.OK
    DeskSentryApp._render_tray(controller, controller.window.settings, Status.ALERT)
    assert "中斷" not in controller.tray.setToolTip.call_args.args[0]
    controller.tray.showMessage.assert_not_called()
