from desk_sentry.actions import (
    AppWindow,
    HideToggle,
    RestorePlan,
    minimize_all_windows,
    minimize_apps,
    restore_windows,
)


def test_minimize_apps_targets_once_skips_iconic_and_reports_only_actual_changes() -> None:
    windows = [
        AppWindow(1, "Inbox", "chrome.exe"),
        AppWindow(2, "Video", "chrome.exe"),
        AppWindow(3, "Notes", "notepad.exe"),
        AppWindow(4, "Admin", "chrome.exe"),
    ]
    iconic = {2}
    calls: list[int] = []

    def minimize(hwnd: int) -> None:
        calls.append(hwnd)
        if hwnd != 4:  # elevated window: request is ignored by Windows
            iconic.add(hwnd)

    changed = minimize_apps(
        (" Chrome.exe ", "chrome.exe"),
        windows=lambda: windows,
        minimize=minimize,
        is_iconic=lambda hwnd: hwnd in iconic,
        is_zoomed=lambda hwnd: False,
    )

    assert calls == [1, 4]
    assert changed == {1: False}


def test_minimize_all_windows_reports_only_newly_iconic_windows() -> None:
    windows = [AppWindow(1, "One", "one.exe"), AppWindow(2, "Two", "two.exe")]
    iconic = {2}

    changed = minimize_all_windows(
        windows=lambda: windows,
        minimize=iconic.add,
        is_iconic=lambda hwnd: hwnd in iconic,
        is_zoomed=lambda hwnd: False,
    )

    assert changed == {1: False}


def test_restore_windows_restores_only_existing_iconic_handles_without_focus() -> None:
    existing = {1, 2}
    iconic = {1, 3}
    calls: list[int] = []

    def restore(hwnd: int, _was_maximized: bool) -> None:
        calls.append(hwnd)
        iconic.discard(hwnd)

    count = restore_windows(
        {1: False, 2: False, 3: False},
        exists=lambda hwnd: hwnd in existing,
        is_iconic=lambda hwnd: hwnd in iconic,
        restore=restore,
    )

    assert count == 1
    assert calls == [1]


def test_restore_plan_drains_at_exact_quiet_boundary() -> None:
    plan = RestorePlan()
    plan.record({10: False, 11: True})

    assert plan.update("clear", 1.0) == {}
    assert plan.update("watching", 5.9) == {}
    assert plan.update("watching", 6.0) == {10: False, 11: True}
    assert plan.pending == {}


def test_restore_plan_alert_resets_timer_and_unions_new_handles() -> None:
    plan = RestorePlan()
    plan.record({10: True})
    plan.update("clear", 1.0)
    plan.record({11: False, 10: False})
    plan.update("alert", 5.9)

    assert plan.update("clear", 6.0) == {}
    assert plan.update("clear", 10.9) == {}
    assert plan.update("clear", 11.0) == {10: True, 11: False}


def test_restore_plan_away_discards_pending_handles() -> None:
    plan = RestorePlan()
    plan.record({10: True})

    assert plan.update("away", 1.0) == {}
    assert plan.pending == {}
    assert plan.update("clear", 10.0) == {}


def test_restore_plan_nonquiet_status_resets_elapsed_quiet_time() -> None:
    plan = RestorePlan()
    plan.record({10: False})
    plan.update("clear", 1.0)
    plan.update("paused", 5.9)

    assert plan.update("watching", 6.0) == {}
    assert plan.update("watching", 10.9) == {}
    assert plan.update("watching", 11.0) == {10: False}


def test_hide_toggle_keeps_its_own_handles_and_restores_on_second_press() -> None:
    minimized: list[tuple[str, ...]] = []
    restored: list[dict[int, bool]] = []

    def minimize_selected(exes: tuple[str, ...]) -> dict[int, bool]:
        minimized.append(exes)
        return {4: True, 5: False}

    toggle = HideToggle(
        minimize_selected=minimize_selected,
        minimize_all=lambda: {99: False},
        restore=lambda hwnds: restored.append(hwnds) or len(hwnds),
    )

    assert toggle.toggle(("mail.exe", "chat.exe"))
    assert toggle.stored == {4: True, 5: False}
    assert toggle.toggle(("ignored.exe",))
    assert minimized == [("mail.exe", "chat.exe")]
    assert restored == [{4: True, 5: False}]
    assert toggle.stored == {}


def test_hide_toggle_with_empty_targets_uses_minimize_all() -> None:
    calls: list[str] = []
    toggle = HideToggle(
        minimize_selected=lambda _exes: (_ for _ in ()).throw(AssertionError()),
        minimize_all=lambda: calls.append("all") or {7: True},
        restore=lambda _hwnds: 1,
    )

    assert toggle.toggle(())
    assert calls == ["all"]
    assert toggle.stored == {7: True}
