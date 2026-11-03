"""Cover selection and restoration never call real Win32 APIs in tests."""

from desk_sentry.actions import AppWindow, ForegroundPlan, bring_cover, restore_foreground


def test_cover_chooses_topmost_visible_target_and_records_previous_foreground():
    activated = []
    result = bring_cover(
        "COVER.EXE",
        windows=lambda: [
            AppWindow(10, "lower", "cover.exe"),
            AppWindow(20, "upper", "cover.exe"),
            AppWindow(30, "other", "other.exe"),
        ],
        z_order=lambda: [30, 20, 10],
        is_iconic=lambda _handle: False,
        foreground=lambda: 99,
        activate=lambda handle: activated.append(handle) or True,
    )
    assert result.found and result.activated and result.previous == 99 and result.target == 20
    assert activated == [20]


def test_missing_or_minimized_cover_does_not_change_foreground():
    result = bring_cover(
        "cover.exe",
        windows=lambda: [AppWindow(10, "hidden", "cover.exe")],
        is_iconic=lambda _handle: True,
        z_order=lambda: [],
        foreground=lambda: 99,
        activate=lambda _handle: (_ for _ in ()).throw(AssertionError("must not activate")),
    )
    assert not result.found and not result.activated and result.previous is None


def test_os_denied_foreground_is_not_reported_as_a_success_or_restored():
    result = bring_cover(
        "cover.exe",
        windows=lambda: [AppWindow(10, "visible", "cover.exe")],
        is_iconic=lambda _handle: False,
        z_order=lambda: [10],
        foreground=lambda: 99,
        activate=lambda _handle: False,
    )
    assert result.found and not result.activated and result.previous is None
    assert not restore_foreground(99, exists=lambda _handle: False, activate=lambda _handle: True)
    assert restore_foreground(
        99,
        exists=lambda _handle: True,
        activate=lambda _handle: True,
        is_iconic=lambda _handle: False,
    )


def test_foreground_restore_unminimizes_previous_window_before_activation():
    events = []
    assert restore_foreground(
        99,
        exists=lambda _handle: True,
        is_iconic=lambda _handle: True,
        restore=lambda handle: events.append(("restore", handle)),
        activate=lambda handle: events.append(("activate", handle)) or True,
    )
    assert events == [("restore", 99), ("activate", 99)]


def test_foreground_restoration_waits_existing_quiet_period_and_away_discards():
    plan = ForegroundPlan()
    plan.record(99)
    assert plan.update("alert", 0.0) is None
    assert plan.update("notice", 1.0) is None
    assert plan.update("watching", 5.9) is None
    assert plan.update("clear", 6.0) == 99
    assert plan.update("clear", 7.0) is None
    plan.record(100)
    assert plan.update("away", 8.0) is None
    assert plan.update("clear", 100.0) is None
