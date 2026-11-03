"""Focused review regressions for Task 03d."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from desk_sentry.ui.controls import Stepper


def test_stepper_has_full_width_numeric_region_and_shows_max_seconds(qtbot: QtBot) -> None:
    stepper = Stepper()
    qtbot.addWidget(stepper)
    stepper.setRange(0, 600)
    stepper.setSuffix(" 秒")
    stepper.setValue(600)
    stepper.show()
    QApplication.processEvents()

    assert stepper.width() == 112
    assert stepper.minus.width() == 28
    assert stepper.spin.width() == 56
    assert stepper.plus.width() == 28
    assert stepper.spin.text() == "600 秒"
    text_width = stepper.spin.fontMetrics().horizontalAdvance(stepper.spin.text())
    editor = stepper.spin.lineEdit()
    assert editor is not None
    stepper.spin.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert text_width <= editor.contentsRect().width()
    # Moving the caret to either end must leave the entire value in the viewport.
    for position in (0, len(editor.text())):
        editor.setCursorPosition(position)
        assert editor.cursorPositionAt(QPoint(0, editor.height() // 2)) == 0
        assert editor.cursorPositionAt(QPoint(editor.width() - 1, editor.height() // 2)) == len(
            editor.text()
        )
