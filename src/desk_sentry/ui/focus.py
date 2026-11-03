"""Show focus styling only after keyboard navigation or shortcut activation."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QWidget


def _mark(widget: QWidget, visible: bool) -> None:
    if bool(widget.property("keyboardFocus")) == visible:
        return
    widget.setProperty("keyboardFocus", visible)
    widget.setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, visible)
    widget.window().setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, visible)
    widget.setStyleSheet(widget.styleSheet())
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _FocusOrigin(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if isinstance(watched, QWidget):
            if event.type() == QEvent.Type.FocusIn and isinstance(event, QFocusEvent):
                _mark(
                    watched,
                    event.reason()
                    in (
                        Qt.FocusReason.TabFocusReason,
                        Qt.FocusReason.BacktabFocusReason,
                        Qt.FocusReason.ShortcutFocusReason,
                    ),
                )
            elif event.type() == QEvent.Type.FocusOut:
                _mark(watched, False)
            elif event.type() == QEvent.Type.MouseButtonPress:
                focused = QApplication.focusWidget()
                if focused is not None:
                    _mark(focused, False)
                _mark(watched, False)
        return False


def install_focus_tracking() -> None:
    app = QApplication.instance()
    if app is not None and not app.findChildren(_FocusOrigin):
        tracker = _FocusOrigin(app)
        app.installEventFilter(tracker)
