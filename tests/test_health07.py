"""TASK-07 health signals use elapsed time and recover after a healthy observation."""

import pytest

from desk_sentry.health import HealthMonitor, HealthState, ReconnectBackoff


def test_three_seconds_without_frame_degrades_and_fresh_inference_recovers():
    now = [0.0]
    health = HealthMonitor(clock=lambda: now[0])
    health.resume()
    now[0] = 2.999
    assert health.check() is None
    now[0] = 3.0
    event = health.check()
    assert event.state is HealthState.DEGRADED and event.reason == "no_frame"
    assert health.check() is None
    now[0] = 5.0
    health.frame()
    health.inference(0.1)
    event = health.check()
    assert event.state is HealthState.OK and event.duration_s == 2.0


def test_only_three_consecutive_slow_inferences_degrade():
    health = HealthMonitor(clock=lambda: 0.0)
    health.resume()
    for duration in [2.1, 2.1, 2.0, 2.1, 2.1]:
        health.frame()
        health.inference(duration)
        assert health.check() is None
    health.inference(2.001)
    assert health.check().reason == "slow_inference"
    health.inference(0.1)
    assert health.check().state is HealthState.OK


@pytest.mark.parametrize("reason", ["engine_error", "MemoryError"])
def test_engine_error_remains_degraded_until_successful_inference(reason):
    health = HealthMonitor(clock=lambda: 0.0)
    health.resume()
    health.error(reason)
    assert health.check().reason == reason
    health.frame()
    assert health.check() is None
    assert health.state is HealthState.DEGRADED
    health.inference(0.2)
    assert health.check().state is HealthState.OK


def test_paused_camera_is_not_treated_as_a_health_failure():
    now = [0.0]
    health = HealthMonitor(clock=lambda: now[0])
    health.resume()
    health.pause()
    now[0] = 100.0
    assert health.check() is None and health.state is HealthState.OK
    health.resume()
    assert health.check() is None


def test_camera_reconnection_uses_bounded_backoff_and_resets_on_frame():
    backoff = ReconnectBackoff()
    assert [backoff.failed() for _ in range(6)] == [2.0, 5.0, 10.0, 30.0, 30.0, 30.0]
    backoff.recovered()
    assert backoff.failed() == 2.0
