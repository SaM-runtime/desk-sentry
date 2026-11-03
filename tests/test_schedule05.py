from datetime import datetime

from desk_sentry.schedule import in_window, next_boundary


def dt(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    # 2026-10-12 is a Monday.
    return datetime(2026, 10, 12 + day, hour, minute, second)


def test_ordinary_window_includes_start_and_excludes_end() -> None:
    days = (0,)

    assert in_window(dt(0, 8, 30), "08:30", "18:30", days)
    assert in_window(dt(0, 12), "08:30", "18:30", days)
    assert not in_window(dt(0, 18, 30), "08:30", "18:30", days)


def test_ordinary_window_filters_by_selected_weekday() -> None:
    assert not in_window(dt(1, 12), "08:30", "18:30", (0,))
    assert in_window(dt(1, 12), "08:30", "18:30", (1,))


def test_overnight_window_is_anchored_to_selected_start_day() -> None:
    monday_only = (0,)

    assert in_window(dt(0, 22), "22:00", "06:00", monday_only)
    assert in_window(dt(1, 5, 59, 59), "22:00", "06:00", monday_only)
    assert not in_window(dt(1, 6), "22:00", "06:00", monday_only)
    assert not in_window(dt(1, 22), "22:00", "06:00", monday_only)


def test_equal_times_and_empty_days_describe_no_window() -> None:
    assert not in_window(dt(0, 8, 30), "08:30", "08:30", (0,))
    assert next_boundary(dt(0, 8), "08:30", "08:30", (0,)) is None
    assert next_boundary(dt(0, 8), "08:30", "18:30", ()) is None


def test_next_boundary_is_strictly_future_for_ordinary_window() -> None:
    days = (0, 2)

    assert next_boundary(dt(0, 8), "08:30", "18:30", days) == dt(0, 8, 30)
    assert next_boundary(dt(0, 8, 30), "08:30", "18:30", days) == dt(0, 18, 30)
    assert next_boundary(dt(0, 18, 30), "08:30", "18:30", days) == dt(2, 8, 30)


def test_next_boundary_handles_overnight_end_and_next_selected_start() -> None:
    monday_only = (0,)

    assert next_boundary(dt(0, 23), "22:00", "06:00", monday_only) == dt(1, 6)
    assert next_boundary(dt(1, 6), "22:00", "06:00", monday_only) == dt(7, 22)
