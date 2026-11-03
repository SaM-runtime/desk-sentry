"""Task 05c regressions for live privacy-curtain screen changes."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from desk_sentry.ui import motion
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow


@dataclass(eq=False, frozen=True)
class _FakeScreen:
    name: str
    bounds: QRect

    def geometry(self) -> QRect:
        return self.bounds


class _ScreenEvents(QObject):
    screenAdded = Signal(object)
    screenRemoved = Signal(object)

    def __init__(self, app: QGuiApplication) -> None:
        super().__init__()
        self._app = app

    def processEvents(self) -> None:  # noqa: N802 - Qt-compatible test proxy
        self._app.processEvents()


class _MirrorSpy(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.raise_count = 0

    def raise_(self) -> None:
        self.raise_count += 1


def _patch_desktop_side_effects(monkeypatch) -> list[_CurtainWindow]:
    closed: list[_CurtainWindow] = []

    def bind_screen(window: _CurtainWindow, screen: object) -> None:
        window._test_screen = screen  # type: ignore[attr-defined]

    def show_without_fullscreen(window: _CurtainWindow) -> None:
        window._test_fullscreen = True  # type: ignore[attr-defined]
        window.show()

    def close_window(window: _CurtainWindow) -> bool:
        closed.append(window)
        window.hide()
        return True

    monkeypatch.setattr(_CurtainWindow, "setScreen", bind_screen)
    monkeypatch.setattr(_CurtainWindow, "showFullScreen", show_without_fullscreen)
    monkeypatch.setattr(_CurtainWindow, "activateWindow", lambda _window: None)
    monkeypatch.setattr(_CurtainWindow, "close", close_window)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    return closed


def test_visible_curtain_adds_new_screen_and_keeps_existing_window(
    qtbot: QtBot, monkeypatch
) -> None:
    closed = _patch_desktop_side_effects(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", QRect(0, 0, 800, 600))
    added = _FakeScreen("added", QRect(800, 0, 1024, 768))
    mirror = _MirrorSpy()
    qtbot.addWidget(mirror)
    mirror.show()
    group = CurtainGroup(mirror=mirror, screens=(first,))  # type: ignore[arg-type]
    group.show("Ctrl+Alt+H")
    original = group.windows[0]
    qtbot.addWidget(original)
    raises_before_add = mirror.raise_count

    events.screenAdded.emit(added)

    assert len(group.windows) == 2
    assert group.windows[0] is original
    assert group.windows[1]._test_screen is added  # type: ignore[attr-defined]
    assert group.windows[1]._test_fullscreen  # type: ignore[attr-defined]
    assert group.windows[1].windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert group.windows[1].windowFlags() & Qt.WindowType.Tool
    assert mirror.raise_count > raises_before_add
    assert closed == []


def test_visible_curtain_removes_only_matching_screen_and_rehomes_shortcut(
    qtbot: QtBot, monkeypatch
) -> None:
    closed = _patch_desktop_side_effects(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", QRect(0, 0, 800, 600))
    second = _FakeScreen("second", QRect(800, 0, 1024, 768))
    replacement = _FakeScreen("replacement", QRect(0, 0, 1280, 720))
    group = CurtainGroup(screens=(first, second))  # type: ignore[arg-type]
    group.show("Ctrl+Alt+H")
    first_window, second_window = group.windows
    qtbot.addWidget(first_window)
    qtbot.addWidget(second_window)

    events.screenRemoved.emit(first)

    assert group.windows == [second_window]
    assert closed == [first_window]
    assert len(group._shortcuts) == 1
    assert group._shortcuts[0].parent() is second_window

    events.screenAdded.emit(replacement)

    assert len(group.windows) == 2
    assert group.windows[0] is second_window
    assert group.windows[1]._test_screen is replacement  # type: ignore[attr-defined]


def test_visible_zero_screen_curtain_updates_shortcut_and_disconnects_when_hidden(
    qtbot: QtBot, monkeypatch
) -> None:
    closed = _patch_desktop_side_effects(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    added = _FakeScreen("added", QRect(0, 0, 800, 600))
    ignored = _FakeScreen("ignored", QRect(800, 0, 800, 600))
    group = CurtainGroup(screens=())
    group.show("Ctrl+Alt+H")

    assert group.visible
    assert group.windows == []
    assert group._shortcuts == []

    events.screenAdded.emit(added)
    only_window = group.windows[0]
    qtbot.addWidget(only_window)
    assert len(group._shortcuts) == 1
    assert group._shortcuts[0].parent() is only_window

    events.screenRemoved.emit(added)
    assert group.windows == []
    assert group._shortcuts == []
    assert closed == [only_window]

    group.hide()
    events.screenAdded.emit(ignored)
    assert group.windows == []


def test_show_reconciles_equal_count_screen_replacement_and_reuses_survivor(
    qtbot: QtBot, monkeypatch
) -> None:
    closed = _patch_desktop_side_effects(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", QRect(0, 0, 800, 600))
    survivor = _FakeScreen("survivor", QRect(800, 0, 800, 600))
    replacement = _FakeScreen("replacement", QRect(0, 0, 1024, 768))
    current_screens = [first, survivor]
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: tuple(current_screens)))
    group = CurtainGroup()
    group.show("")
    first_window, survivor_window = group.windows
    qtbot.addWidget(first_window)
    qtbot.addWidget(survivor_window)
    group.hide()

    current_screens[:] = [replacement, survivor]
    group.show("")

    assert len(group.windows) == 2
    assert group.windows[1] is survivor_window
    assert group.windows[0] is not first_window
    assert group.windows[0]._test_screen is replacement  # type: ignore[attr-defined]
    assert closed == [first_window]
