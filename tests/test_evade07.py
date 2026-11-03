"""Automatic evasion confirms a sustained alert and suppresses repeated episodes."""

from desk_sentry.actions import EvadePlan


def test_short_alert_or_not_near_never_executes():
    plan = EvadePlan()
    assert not plan.update("alert", 0.0, alert_started=True, eligible=True)
    assert not plan.update("alert", 0.399, eligible=True)
    assert not plan.update("clear", 0.4, eligible=True)
    assert not plan.update("alert", 1.0, alert_started=True, eligible=False)
    assert not plan.update("alert", 1.4, eligible=False)


def test_confirmed_alert_runs_once_and_cooldown_drops_whole_new_episode():
    plan = EvadePlan()
    assert not plan.update("alert", 0.0, alert_started=True, eligible=True)
    assert plan.update("approaching", 0.4, eligible=True)
    assert not plan.update("alert", 1.0, eligible=True)
    assert not plan.update("clear", 2.0)
    assert not plan.update("alert", 10.0, alert_started=True, eligible=True)
    assert not plan.update("alert", 30.4, eligible=True)
    plan.update("clear", 31.0)
    assert not plan.update("alert", 31.0, alert_started=True, eligible=True)
    assert plan.update("alert", 31.4, eligible=True)


def test_later_height_confirmation_can_execute_only_inside_original_alert():
    plan = EvadePlan()
    plan.update("alert", 0.0, alert_started=True, eligible=False)
    assert not plan.update("alert", 0.4, eligible=False)
    assert plan.update("alert", 0.5, eligible=True)
    assert not plan.update("watching", 1.0)
    assert not plan.update("alert", 40.0, eligible=True)
