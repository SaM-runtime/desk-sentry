from types import SimpleNamespace

from desk_sentry import actions
from desk_sentry.actions import AwayPlan


def test_away_action_fires_at_exact_deadline_once_and_resets_after_return():
    plan = AwayPlan()
    args = {"action": "lock", "minutes": 2, "last_input_age": lambda: 60.0}
    assert plan.update("away", 10.0, **args) is None
    assert plan.update("away", 129.0, **args) is None
    assert plan.update("away", 130.0, **args) == "lock"
    assert plan.update("away", 300.0, **args) is None
    assert plan.update("clear", 301.0, **args) is None
    assert plan.update("away", 302.0, **args) is None
    assert plan.update("away", 422.0, **args) == "lock"


def test_recent_input_delays_away_action_for_a_complete_new_period():
    age = 59.0
    plan = AwayPlan()
    args = {"action": "lock", "minutes": 1, "last_input_age": lambda: age}
    plan.update("away", 0.0, **args)
    assert plan.update("away", 60.0, **args) is None
    age = 60.0
    assert plan.update("away", 119.0, **args) is None
    assert plan.update("away", 120.0, **args) == "lock"


def test_away_minimize_requires_targets_and_none_never_executes():
    for action, targets, expected in (
        ("minimize_app", (), None),
        ("minimize_app", ("mail.exe",), "minimize_app"),
        ("none", ("mail.exe",), None),
    ):
        plan = AwayPlan()
        args = {
            "action": action,
            "minutes": 1,
            "target_apps": targets,
            "last_input_age": lambda: 120.0,
        }
        plan.update("away", 0.0, **args)
        assert plan.update("away", 60.0, **args) == expected


def test_input_age_reads_timestamp_only_and_handles_tick_wrap_and_unavailable(monkeypatch):
    def last(info):
        info._obj.dwTime = 0xFFFFFFF0
        return True

    native = SimpleNamespace(
        user32=SimpleNamespace(GetLastInputInfo=last),
        kernel32=SimpleNamespace(GetTickCount=lambda: 984),
    )
    monkeypatch.setattr(actions.ctypes, "windll", native)
    assert actions.last_input_age() == 1.0
    native.user32.GetLastInputInfo = lambda info: False
    assert actions.last_input_age() == 0.0
