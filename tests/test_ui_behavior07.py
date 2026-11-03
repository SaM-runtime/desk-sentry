"""Automatic pause copy and mini-mirror placement/save behavior at real Qt seams."""

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication

from desk_sentry.config import Settings
from desk_sentry.ui import mini_mirror as mini_ui
from desk_sentry.ui.main_window import MainWindow
from desk_sentry.ui.mini_mirror import MiniMirror


@pytest.mark.parametrize("reason", ["locked", "schedule"])
def test_automatic_pause_pill_survives_hover_and_restores_monitoring_copy(qtbot, reason):
    window = MainWindow(Settings(armed=True), lambda _settings: None)
    qtbot.addWidget(window)
    window.show()
    window.set_pause_reason(reason)
    assert window.arm_button.text() == "自動暫停"
    QApplication.sendEvent(window.arm_button, QEnterEvent(QPointF(), QPointF(), QPointF()))
    assert window.arm_button.text() == "自動暫停"
    QApplication.sendEvent(window.arm_button, QEvent(QEvent.Type.Leave))
    assert window.arm_button.text() == "自動暫停" and window.settings.armed
    window.set_pause_reason(None)
    assert window.arm_button.text() == "監看中"
    window.set_armed(False)
    assert window.arm_button.text() == "開始監看"


class Screen:
    def __init__(self, bounds):
        self.bounds = bounds

    def availableGeometry(self):  # noqa: N802 - injected screen boundary
        return QRect(self.bounds)


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, screen):
        for callback in self.callbacks:
            callback(screen)


class Screens:
    def __init__(self):
        self.screenAdded = Signal()
        self.screenRemoved = Signal()


def test_offscreen_show_and_screen_removal_return_mirror_to_primary(qtbot, monkeypatch):
    primary = Screen(QRect(0, 0, 1920, 1080))
    secondary = Screen(QRect(1920, 0, 1920, 1080))
    current = [primary, secondary]
    monkeypatch.setattr(mini_ui.QGuiApplication, "screens", lambda: list(current))
    monkeypatch.setattr(mini_ui.QGuiApplication, "primaryScreen", lambda: primary)
    source = Screens()
    mini = MiniMirror(screen_source=source)
    qtbot.addWidget(mini)
    mini.move(6000, 5000)
    mini.show()
    QApplication.processEvents()
    assert primary.bounds.contains(mini.geometry())
    mini.restore((2200, 50, 320, 180))
    assert secondary.bounds.contains(mini.geometry())
    current.remove(secondary)
    source.screenRemoved.emit(secondary)
    assert primary.bounds.contains(mini.geometry())
    mini.move(6000, 5000)
    source.screenAdded.emit(secondary)
    assert primary.bounds.contains(mini.geometry())


def test_drag_saves_once_three_hundred_ms_after_release(qtbot):
    mini = MiniMirror()
    qtbot.addWidget(mini)
    saved = []
    mini.on_geometry = saved.append
    mini.restore(None)
    mini.show()
    qtbot.wait(350)
    saved.clear()
    qtbot.mousePress(mini, Qt.MouseButton.LeftButton, pos=mini.rect().center())
    for _offset in range(3):
        mini.move(mini.pos() + QPoint(1, 1))
        qtbot.wait(150)
    assert saved == []
    qtbot.mouseRelease(mini, Qt.MouseButton.LeftButton, pos=mini.rect().center())
    qtbot.wait(250)
    assert saved == []
    qtbot.wait(100)
    assert len(saved) == 1


def test_native_move_loop_saves_once_after_exit_message(qtbot):
    import ctypes
    from ctypes import wintypes

    mini = MiniMirror()
    qtbot.addWidget(mini)
    saved = []
    mini.on_geometry = saved.append
    mini.restore(None)
    mini.show()
    qtbot.wait(350)
    saved.clear()
    packet = wintypes.MSG()
    packet.message = 0x0231
    mini.nativeEvent(b"windows_generic_MSG", ctypes.addressof(packet))
    mini.move(mini.pos() + QPoint(5, 5))
    qtbot.wait(400)
    assert saved == []
    packet.message = 0x0232
    mini.nativeEvent(b"windows_generic_MSG", ctypes.addressof(packet))
    qtbot.wait(250)
    assert saved == []
    qtbot.wait(100)
    assert len(saved) == 1
    qtbot.wait(350)
    assert len(saved) == 1
