from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from desk_sentry.ui import history as history_ui
from desk_sentry.ui.history import AlertHistory, HistoryWindow, _Thumb


def _frame(value: int) -> np.ndarray:
    return np.full((100, 160, 3), value, dtype=np.uint8)


def _thumbs(window: HistoryWindow) -> list[_Thumb]:
    return window.findChildren(_Thumb)


def test_history_default_retains_thirty_original_images_in_memory() -> None:
    history = AlertHistory()
    for value in range(35):
        history.add(_frame(value))

    items = history.items()
    assert len(items) == 30
    assert int(items[0].thumbnail[0, 0, 0]) == 34
    assert int(items[-1].thumbnail[0, 0, 0]) == 5


def test_discreet_history_masks_all_images_and_reveals_only_clicked_one(qtbot: QtBot) -> None:
    history = AlertHistory()
    history.add(_frame(20))
    history.add(_frame(80))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.show()
    window.refresh()

    thumbs = _thumbs(window)
    assert window.windowTitle() == "紀錄"
    assert len(thumbs) == 2
    assert all(thumb.pixmap().isNull() for thumb in thumbs)

    qtbot.mouseClick(thumbs[0], Qt.MouseButton.LeftButton)
    assert not thumbs[0].pixmap().isNull()
    assert thumbs[1].pixmap().isNull()
    assert int(history.items()[0].thumbnail[0, 0, 0]) == 80


def test_discreet_history_first_click_only_reveals_second_click_can_label(qtbot: QtBot) -> None:
    history = AlertHistory()
    history.add(_frame(40), shot_id=7, face=np.ones(128, dtype=np.float32))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    labelled: list[int] = []
    window.on_label = lambda item: labelled.append(item.shot_id)
    window.show()
    window.refresh()
    thumb = _thumbs(window)[0]

    qtbot.mouseClick(thumb, Qt.MouseButton.LeftButton)
    assert labelled == [] and not thumb.pixmap().isNull()
    qtbot.mouseClick(thumb, Qt.MouseButton.LeftButton)
    assert labelled == [7]


def test_discreet_history_revealed_image_remasks_after_timeout(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history_ui, "DISCREET_REVEAL_MS", 25)
    history = AlertHistory()
    history.add(_frame(60))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.show()
    window.refresh()
    thumb = _thumbs(window)[0]

    qtbot.mouseClick(thumb, Qt.MouseButton.LeftButton)
    assert not thumb.pixmap().isNull()
    qtbot.waitUntil(lambda: thumb.pixmap().isNull(), timeout=500)


def test_discreet_history_refresh_and_new_alert_do_not_extend_or_leak_reveal(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history_ui, "DISCREET_REVEAL_MS", 50)
    history = AlertHistory()
    history.add(_frame(60))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.show()
    window.refresh()
    qtbot.mouseClick(_thumbs(window)[0], Qt.MouseButton.LeftButton)

    history.add(_frame(90))
    window.refresh()
    assert sum(not thumb.pixmap().isNull() for thumb in _thumbs(window)) == 1

    for _ in range(5):
        qtbot.wait(15)
        window.refresh()
    assert all(thumb.pixmap().isNull() for thumb in _thumbs(window))


@pytest.mark.parametrize("close_method", ["hide", "close"])
def test_discreet_history_hiding_or_closing_masks_before_reopen(
    qtbot: QtBot, close_method: str
) -> None:
    history = AlertHistory()
    history.add(_frame(100))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.show()
    window.refresh()
    thumb = _thumbs(window)[0]
    qtbot.mouseClick(thumb, Qt.MouseButton.LeftButton)
    assert not thumb.pixmap().isNull()

    getattr(window, close_method)()
    assert thumb.pixmap().isNull()
    window.show()
    window.refresh()
    assert all(item.pixmap().isNull() for item in _thumbs(window))


def test_discreet_toggle_resets_reveals_and_restores_original_behavior(qtbot: QtBot) -> None:
    history = AlertHistory()
    history.add(_frame(120))
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.show()
    window.refresh()
    qtbot.mouseClick(_thumbs(window)[0], Qt.MouseButton.LeftButton)

    window.set_discreet(False)
    assert window.windowTitle() == "Desk Sentry 最近提醒"
    assert all(not thumb.pixmap().isNull() for thumb in _thumbs(window))

    window.set_discreet(True)
    assert window.windowTitle() == "紀錄"
    assert all(thumb.pixmap().isNull() for thumb in _thumbs(window))


@pytest.mark.parametrize("discreet", [False, True])
def test_history_past_caption_shows_date_in_normal_and_discreet_modes(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, discreet: bool
) -> None:
    now = datetime(2026, 10, 13, 15, 30, 0).astimezone()
    monkeypatch.setattr(history_ui.time, "time", lambda: now.timestamp())
    moments = iter((now.timestamp(), (now - timedelta(days=1)).timestamp()))
    history = AlertHistory(clock=lambda: next(moments))
    history.add(_frame(10))
    history.add(_frame(20))
    window = HistoryWindow(history, discreet=discreet)
    qtbot.addWidget(window)
    window.refresh()

    thumbs = _thumbs(window)
    if discreet:
        assert sorted(thumb.toolTip() for thumb in thumbs) == ["10-12 15:30:00", "15:30:00"]
    else:
        assert {thumb.toolTip() for thumb in thumbs} == {"沒拍到清楚的正臉，無法用來辨識"}
    caption_tooltips = [
        (label.toolTip(), label.text())
        for label in window.findChildren(QLabel)
        if type(label) is QLabel and label.toolTip()
    ]
    assert dict(caption_tooltips) == {
        "10-12 15:30:00": "10-12 15:30:00",
        "15:30:00": "剛剛",
    }
    assert all(thumb.pixmap().isNull() is discreet for thumb in thumbs)
