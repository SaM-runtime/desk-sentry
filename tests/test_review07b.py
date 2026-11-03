"""TASK-07b reproduced app, health, and startup failures at existing seams."""

from unittest.mock import Mock

import pytest

from desk_sentry import actions, config, hotkey
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.health import HealthMonitor, HealthState
from desk_sentry.people import Level
from desk_sentry.sentry import Role, Status
from test_app_hide04 import app_with
from test_app_review05c import local_app as local_app

pytestmark = pytest.mark.usefixtures("qapp")


def test_error_during_confirmation_preserves_pending_and_executes_once_after_recovery(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("desk_sentry.app.time.monotonic", lambda: now[0])
    execute = Mock()
    monkeypatch.setattr(actions, "run_action", execute)
    controller = app_with(Settings(action="lock", sound=False))
    track = TrackView(1, (0.2, 0.1, 0.5, 0.6), Role.INTRUDER, 0.9)
    controller._on_snapshot(
        Snapshot(Status.ALERT, tracks=(track,), alert_level=Level.HIGH, alert_started=True)
    )
    now[0] = 0.2
    controller._on_snapshot(Snapshot(Status.ERROR, health_state=HealthState.DEGRADED))
    execute.assert_not_called()
    now[0] = 0.399
    controller._on_snapshot(Snapshot(Status.ALERT, tracks=(track,), alert_level=Level.HIGH))
    execute.assert_not_called()
    now[0] = 0.799  # TASK-07c restarts 0.4 s on recovery at 0.399.
    controller._on_snapshot(Snapshot(Status.ALERT, tracks=(track,), alert_level=Level.HIGH))
    now[0] = 0.899
    controller._on_snapshot(Snapshot(Status.ALERT, tracks=(track,), alert_level=Level.HIGH))
    execute.assert_called_once_with("lock")


@pytest.mark.parametrize("first_reason", ["slow_inference", "no_frame"])
def test_degraded_reason_changes_preserve_original_outage_duration(first_reason):
    now = [0.0]
    health = HealthMonitor(clock=lambda: now[0])
    health.resume()
    for _ in range(3):
        health.inference(2.1)
    now[0] = 1.0 if first_reason == "slow_inference" else 3.0
    first = health.check()
    assert first.reason == first_reason and first.duration_s == 0
    started = now[0]
    if first_reason == "slow_inference":
        now[0] = 3.0
        expected = "no_frame"
    else:
        now[0] = 4.0
        health.frame()
        expected = "slow_inference"
    health.check()
    assert health.state is HealthState.DEGRADED and health.reason == expected
    now[0] = 5.0
    health.frame()
    health.inference(0.1)
    recovery = health.check()
    assert recovery.state is HealthState.OK and recovery.duration_s == 5.0 - started


@pytest.mark.parametrize("fail_registration", [False, True])
def test_startup_marker_registration_failure_shows_unavailable_hint(
    local_app, monkeypatch, fail_registration
):
    build, path, _started = local_app
    config.save(Settings(mark_false_hotkey="Ctrl+Alt+F", mark_real_hotkey="Ctrl+Alt+R"), path)
    original_register = hotkey.Win32Backend

    def backend(registration_id):
        result = original_register(registration_id)
        result.register = Mock(return_value=not fail_registration)
        return result

    monkeypatch.setattr(hotkey, "Win32Backend", backend)
    controller = build()
    try:
        for hint in controller.window._mark_hotkey_hints.values():
            assert (hint.text() == "快捷鍵無法使用") is fail_registration
    finally:
        controller.mark_false_hotkey.close()
        controller.mark_real_hotkey.close()
