"""Task 06 plain privacy curtain behavior."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from desk_sentry.ui import motion, theme
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow


def _show_without_covering_desktop(self: _CurtainWindow) -> None:
    self.setWindowState(self.windowState() | Qt.WindowState.WindowFullScreen)


def _patch_window_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(_CurtainWindow, "showFullScreen", _show_without_covering_desktop)
    monkeypatch.setattr(_CurtainWindow, "activateWindow", lambda _self: None)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)


def test_discreet_hides_name_but_keeps_the_way_out(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_boundaries(monkeypatch)
    group = CurtainGroup(screens=tuple(QGuiApplication.screens()))
    group.show("Ctrl+Alt+H")
    window = group.windows[0]
    qtbot.addWidget(window)

    group.set_discreet(True)
    assert window.title.isHidden()
    # A plain black screen left testers unable to find the exit.
    assert not window.instruction.isHidden()
    assert window.instruction.text() == "按 Esc、Ctrl+Alt+H 或連按兩下移開"
    assert theme.TEXT_3 in window.instruction.styleSheet()

    group.set_discreet(False)
    assert not window.title.isHidden()
    assert not window.instruction.isHidden()
    assert window.title.text() == "Desk Sentry"
    assert window.instruction.text() == "按 Esc、Ctrl+Alt+H 或連按兩下移開"


def test_discreet_applies_to_future_windows(qtbot: QtBot, monkeypatch) -> None:
    _patch_window_boundaries(monkeypatch)
    screens = tuple(QGuiApplication.screens())
    group = CurtainGroup(screens=screens)
    group.set_discreet(True)
    group.show("Ctrl+Alt+H")
    window = group.windows[0]
    qtbot.addWidget(window)

    assert window.title.isHidden()
    assert not window.instruction.isHidden()
