from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pytestqt.qtbot import QtBot

from desk_sentry.stats import AlertStats
from desk_sentry.ui import theme
from desk_sentry.ui.history import AlertHistory, HistoryWindow

NOW = datetime(2026, 10, 12, 14, 30)


def test_history_summary_shows_empty_state_and_refreshes_counts(
    qtbot: QtBot, tmp_path: Path
) -> None:
    stats = AlertStats(tmp_path / "alert-stats.json", clock=lambda: NOW)
    window = HistoryWindow(AlertHistory(), stats=stats)
    qtbot.addWidget(window)

    window.refresh()
    assert window.today_summary.text() == "今天還沒有警示"
    assert window.seven_day_summary.text() == "近 7 天 0 次"

    stats.record(alert_started=True)
    window.refresh()
    assert window.today_summary.text() == "今天 1 次"
    assert window.seven_day_summary.text() == "近 7 天 1 次"


def test_history_summary_chart_scales_hours_and_marks_current_hour(
    qtbot: QtBot, tmp_path: Path
) -> None:
    stats = AlertStats(tmp_path / "alert-stats.json", clock=lambda: NOW)
    for hour in (9, 14, 14, 14):
        stats.record(alert_started=True, now=NOW.replace(hour=hour))
    window = HistoryWindow(AlertHistory(), stats=stats)
    qtbot.addWidget(window)
    window.refresh()

    chart = window.hourly_chart
    assert chart.bar_height(0) == 1
    assert 1 < chart.bar_height(9) < chart.bar_height(14)
    assert chart.hour_color(14) == theme.ACCENT
    assert chart.hour_color(13) == theme.TEXT_2
    assert chart.hour_tooltip(14) == "14 時：3 次"


def test_history_without_stats_keeps_original_constructor_contract(qtbot: QtBot) -> None:
    window = HistoryWindow(AlertHistory())
    qtbot.addWidget(window)

    window.refresh()
    assert not window.summary_row.isVisibleTo(window)
