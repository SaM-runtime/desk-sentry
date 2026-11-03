"""History must keep its controls and every thumbnail reachable on the chosen screen."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from desk_sentry.ui import history as history_ui
from desk_sentry.ui import theme
from desk_sentry.ui.history import AlertHistory, HistoryWindow, _Thumb


class Screen:
    def __init__(self, area):
        self.area = area

    def availableGeometry(self):  # noqa: N802 - injected screen boundary
        return QRect(self.area)


@pytest.fixture
def styled_app(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(theme.STYLESHEET)
    yield qapp
    qapp.setStyleSheet(previous)


def make_history(count):
    history = AlertHistory()
    for value in range(count):
        history.add(np.full((100, 160, 3), value, dtype=np.uint8))
    return history


@pytest.mark.parametrize("height", [1080, 768])
def test_thirty_masked_thumbnails_fit_screen_and_last_row_is_reachable(
    qtbot, monkeypatch, styled_app, height
):
    bounds = QRect(-1920, -100, 1920, height)
    monkeypatch.setattr(history_ui.QGuiApplication, "primaryScreen", lambda: Screen(bounds))
    window = HistoryWindow(make_history(30), discreet=True)
    qtbot.addWidget(window)
    window.open_near()
    QApplication.processEvents()

    assert window.width() == 760
    assert window.height() <= bounds.height()
    assert bounds.contains(window.frameGeometry())
    scroll = window.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() > 0
    assert not scroll.isAncestorOf(window.clear_button)
    controls_before = window.clear_button.mapToGlobal(QPoint())
    thumbs = scroll.widget().findChildren(_Thumb)
    assert len(thumbs) == 30 and all(thumb.pixmap().isNull() for thumb in thumbs)

    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    QApplication.processEvents()
    last = thumbs[-1]
    last_rect = QRect(last.mapTo(scroll.viewport(), QPoint()), last.size())
    assert scroll.viewport().rect().contains(last_rect)
    assert window.clear_button.mapToGlobal(QPoint()) == controls_before
    qtbot.mouseClick(last, Qt.MouseButton.LeftButton)
    assert not last.pixmap().isNull()
    window.close()
    assert all(thumb.pixmap().isNull() for thumb in thumbs)


@pytest.mark.parametrize("discreet", [False, True])
def test_ten_thumbnails_keep_original_size_and_need_no_scroll(
    qtbot, monkeypatch, styled_app, discreet
):
    bounds = QRect(0, 0, 1920, 1080)
    monkeypatch.setattr(history_ui.QGuiApplication, "primaryScreen", lambda: Screen(bounds))
    window = HistoryWindow(make_history(10), discreet=discreet)
    qtbot.addWidget(window)
    window.open_near()
    QApplication.processEvents()

    assert window.width() == 760
    assert window.height() < 768
    thumbs = window.findChildren(_Thumb)
    assert len(thumbs) == 10
    assert {(thumb.width(), thumb.height()) for thumb in thumbs} == {(171, 107)}
    assert all(thumb.pixmap().isNull() is discreet for thumb in thumbs)
    scroll = window.findChild(QScrollArea)
    assert scroll is not None and scroll.verticalScrollBar().maximum() == 0
    assert scroll.horizontalScrollBar().maximum() == 0
    assert bounds.contains(window.frameGeometry())


def test_open_near_uses_anchor_screen_instead_of_primary(qtbot, monkeypatch, styled_app):
    primary = Screen(QRect(0, 0, 1366, 768))
    selected = Screen(QRect(1920, 40, 1920, 1040))
    monkeypatch.setattr(history_ui.QGuiApplication, "primaryScreen", lambda: primary)

    class Anchor(QWidget):
        def screen(self):
            return selected

    anchor = Anchor()
    qtbot.addWidget(anchor)
    window = HistoryWindow(make_history(30), discreet=True)
    qtbot.addWidget(window)
    window.open_near(anchor)
    QApplication.processEvents()
    assert selected.area.contains(window.frameGeometry())
    assert window.height() <= selected.area.height()


def test_open_history_growth_stays_bounded_and_clear_returns_empty_layout(
    qtbot, monkeypatch, styled_app
):
    bounds = QRect(0, 0, 1366, 768)
    monkeypatch.setattr(history_ui.QGuiApplication, "primaryScreen", lambda: Screen(bounds))
    history = make_history(10)
    window = HistoryWindow(history, discreet=True)
    qtbot.addWidget(window)
    window.open_near()
    for value in range(20):
        history.add(np.full((100, 160, 3), value, dtype=np.uint8))
    window.refresh()
    QApplication.processEvents()
    assert bounds.contains(window.frameGeometry())
    scrollbar = window.findChild(QScrollArea).verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum())
    position = scrollbar.value()
    window.refresh()
    QApplication.processEvents()
    assert scrollbar.value() == position
    window.clear_button.click()
    QApplication.processEvents()
    assert window.empty_label.isVisible()
    assert not window.findChild(QScrollArea).isVisible()
    assert bounds.contains(window.frameGeometry())
