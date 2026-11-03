import pytest

from desk_sentry.engine import StatusSmoother
from desk_sentry.sentry import Status


@pytest.mark.parametrize(
    ("higher", "lower", "delay"),
    [(Status.APPROACHING, Status.ALERT, 1.5), (Status.WATCHING, Status.CLEAR, 1.0)],
)
def test_downgrade_waits_from_last_raw_high_status_and_expires_at_boundary(
    higher: Status, lower: Status, delay: float
) -> None:
    smoother = StatusSmoother()
    assert smoother.update(higher, 10.0) is higher
    assert smoother.update(higher, 10.5) is higher
    assert smoother.update(lower, 10.5 + delay - 0.001) is higher
    assert smoother.update(lower, 10.5 + delay) is lower
    assert smoother.update(lower, 10.5 + delay + 0.001) is lower


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        (Status.CLEAR, Status.WATCHING),
        (Status.CLEAR, Status.ALERT),
        (Status.CLEAR, Status.APPROACHING),
        (Status.WATCHING, Status.ALERT),
        (Status.WATCHING, Status.APPROACHING),
        (Status.ALERT, Status.APPROACHING),
    ],
)
def test_every_upgrade_is_immediate(lower: Status, higher: Status) -> None:
    smoother = StatusSmoother()
    assert smoother.update(lower, 1.0) is lower
    assert smoother.update(higher, 1.01) is higher


@pytest.mark.parametrize("special", [Status.PAUSED, Status.NO_CAMERA, Status.ERROR, Status.AWAY])
@pytest.mark.parametrize(
    ("higher", "lower"), [(Status.APPROACHING, Status.ALERT), (Status.WATCHING, Status.CLEAR)]
)
def test_special_status_is_immediate_and_resets_pending_downgrade(
    special: Status, higher: Status, lower: Status
) -> None:
    smoother = StatusSmoother()
    assert smoother.update(higher, 1.0) is higher
    assert smoother.update(lower, 1.1) is higher
    assert smoother.update(special, 1.2) is special
    assert smoother.update(lower, 1.3) is lower


def test_alert_ending_does_not_resurrect_approaching_on_next_alert() -> None:
    smoother = StatusSmoother()
    assert smoother.update(Status.APPROACHING, 1.0) is Status.APPROACHING
    assert smoother.update(Status.CLEAR, 1.1) is Status.CLEAR
    assert smoother.update(Status.ALERT, 1.2) is Status.ALERT


def test_alert_clears_old_watching_timer() -> None:
    smoother = StatusSmoother()
    assert smoother.update(Status.WATCHING, 1.0) is Status.WATCHING
    assert smoother.update(Status.CLEAR, 1.1) is Status.WATCHING
    assert smoother.update(Status.ALERT, 1.2) is Status.ALERT
    assert smoother.update(Status.CLEAR, 1.3) is Status.CLEAR
