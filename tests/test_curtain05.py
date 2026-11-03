"""Task 05 full-screen privacy curtain behavior."""

from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from desk_sentry.ui import motion, theme
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow


def _no_desktop_fullscreen(self: _CurtainWindow) -> None:
    """Preserve fullscreen state without covering the Windows test desktop."""

    self.setWindowState(self.windowState() | Qt.WindowState.WindowFullScreen)


def _no_activate(self: _CurtainWindow) -> None:
    return None


def _patch_window_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(_CurtainWindow, "showFullScreen", _no_desktop_fullscreen)
    monkeypatch.setattr(_CurtainWindow, "activateWindow", _no_activate)


class _MirrorSpy(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.raise_count = 0

    def raise_(self) -> None:
        self.raise_count += 1


def test_curtain_show_escape_and_copy(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_side_effects(monkeypatch)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    group = CurtainGroup()
    group.show("Ctrl+Alt+H")
    qtbot.addWidget(group.windows[0])

    assert group.visible
    curtain = group.windows[0]
    assert curtain.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert curtain.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert curtain.windowFlags() & Qt.WindowType.Tool
    assert curtain.isFullScreen()
    assert curtain.styleSheet() == f"background: {theme.STAGE};"
    assert curtain.instruction.text() == "按 Ctrl+Alt+H 或連按兩下移開"
    assert len(group._shortcuts) == 1

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    curtain.keyPressEvent(event)
    assert not group.visible


def test_curtain_double_click_and_toggle(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_side_effects(monkeypatch)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    group = CurtainGroup()
    group.toggle("")
    qtbot.addWidget(group.windows[0])
    assert group.visible
    assert group.windows[0].instruction.text() == "連按兩下或按 Esc 移開"

    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(10, 10),
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    group.windows[0].mouseDoubleClickEvent(event)
    assert not group.visible

    group.toggle("Ctrl+Alt+H")
    assert group.visible
    group.toggle("Ctrl+Alt+H")
    assert not group.visible


def test_curtain_raises_mini_mirror_after_show(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_side_effects(monkeypatch)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    mirror = _MirrorSpy()
    qtbot.addWidget(mirror)
    mirror.show()
    group = CurtainGroup(mirror=mirror)
    group.show("Ctrl+Alt+H")
    qtbot.addWidget(group.windows[0])

    assert mirror.raise_count >= 1


def test_curtain_binds_screen_and_calls_hidden_once(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_side_effects(monkeypatch)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    hidden: list[None] = []
    screens = tuple(QGuiApplication.screens())
    group = CurtainGroup(screens=screens, on_hidden=lambda: hidden.append(None))
    group.show("Ctrl+Alt+H")
    qtbot.addWidget(group.windows[0])

    assert group.windows[0].screen() is screens[0]
    group.hide()
    group.hide()
    assert hidden == [None]


def test_repeated_show_stops_old_animation_before_replacing(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_side_effects(monkeypatch)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    group = CurtainGroup()
    group.show("Ctrl+Alt+H")
    qtbot.addWidget(group.windows[0])
    old = tuple(group._animations)
    assert old

    group.show("Ctrl+Alt+H")

    assert all(animation.state() == QAbstractAnimation.State.Stopped for animation in old)
    assert group.visible
