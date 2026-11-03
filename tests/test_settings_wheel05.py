"""Wheel gestures scroll settings without editing focused or dynamic controls."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QKeySequenceEdit,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from desk_sentry.config import Settings
from desk_sentry.ui.controls import SegmentedControl
from desk_sentry.ui.main_window import MainWindow


@pytest.mark.parametrize("kind", ["combo", "spin", "segments", "time", "hotkey", "slider"])
@pytest.mark.parametrize("focused", [False, True])
def test_wheel_over_setting_scrolls_page_without_changing_value(qtbot, kind, focused):
    window = MainWindow(Settings(), lambda _settings: None)
    qtbot.addWidget(window)
    window.resize(1180, 420)
    window.show()
    content = window.findChild(QWidget, "settingsContent")
    scroll = window.findChild(QScrollArea, "settingsScroll")
    assert content is not None and scroll is not None
    if kind == "combo":
        control = QComboBox()
        control.addItems(["One", "Two", "Three"])
        control.setCurrentIndex(1)
        value = control.currentIndex
    elif kind == "spin":
        control = QSpinBox()
        control.setValue(50)
        value = control.value
    elif kind == "segments":
        control = SegmentedControl([("one", "One"), ("two", "Two")], "one")
        value = control.currentIndex
    elif kind == "time":
        control = QTimeEdit()
        value = control.time
    elif kind == "hotkey":
        control = QKeySequenceEdit()
        value = control.keySequence
    else:
        control = QSlider(Qt.Orientation.Horizontal)
        control.setValue(50)
        value = control.value
    content.layout().insertWidget(0, control)  # added after the settings page was built
    QApplication.processEvents()
    scroll.verticalScrollBar().setValue(100)
    control.setFocus() if focused else content.setFocus()
    before = value()
    position = scroll.verticalScrollBar().value()
    local = control.rect().center()
    event = QWheelEvent(
        QPointF(local),
        QPointF(control.mapToGlobal(local)),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(control, event)
    assert value() == before
    assert scroll.verticalScrollBar().value() > position


def test_show_and_drawer_focus_page_root_but_tab_and_arrows_still_edit(qtbot):
    window = MainWindow(Settings(), lambda _settings: None)
    qtbot.addWidget(window)
    window.show()
    QApplication.processEvents()
    content = window.findChild(QWidget, "settingsContent")
    assert window.focusWidget() is content
    window.target_app.setFocus()
    window.hide()
    window.show()
    QApplication.processEvents()
    assert window.focusWidget() is content
    window.resize(700, 640)
    window.open_drawer()
    QApplication.processEvents()
    assert window.focusWidget() is content
    qtbot.keyClick(content, Qt.Key.Key_Tab)
    assert window.focusWidget() is not content
    window.camera.spin.setFocus(Qt.FocusReason.TabFocusReason)
    before = window.camera.value()
    qtbot.keyClick(window.camera.spin, Qt.Key.Key_Up)
    assert window.camera.value() == before + 1
