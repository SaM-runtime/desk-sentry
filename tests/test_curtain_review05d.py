"""Task 05d regressions for privacy-curtain close semantics."""

from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from desk_sentry.ui import motion
from desk_sentry.ui.curtain import CurtainGroup, _CurtainWindow
from test_curtain_review05c import _FakeScreen, _ScreenEvents


def _patch_fake_screen_windows(monkeypatch) -> None:
    def bind_screen(window: _CurtainWindow, screen: object) -> None:
        window._test_screen = screen  # type: ignore[attr-defined]

    monkeypatch.setattr(_CurtainWindow, "setScreen", bind_screen)
    monkeypatch.setattr(_CurtainWindow, "showFullScreen", _CurtainWindow.show)
    monkeypatch.setattr(_CurtainWindow, "activateWindow", lambda _window: None)
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)


def test_close_events_cannot_dismiss_visible_curtain(qtbot: QtBot, monkeypatch) -> None:
    _patch_fake_screen_windows(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", bounds=app.primaryScreen().geometry())
    second = _FakeScreen("second", bounds=app.primaryScreen().geometry())
    hidden: list[None] = []
    group = CurtainGroup(
        screens=(first, second),  # type: ignore[arg-type]
        on_hidden=lambda: hidden.append(None),
    )
    group.show("Ctrl+Alt+H")
    first_window, second_window = group.windows
    qtbot.addWidget(first_window)
    qtbot.addWidget(second_window)

    close_event = QCloseEvent()
    QApplication.sendEvent(first_window, close_event)
    close_result = second_window.close()

    assert not close_event.isAccepted()
    assert close_result is False
    assert all(window.isVisible() for window in group.windows)
    assert group.visible
    assert hidden == []

    group.hide()
    assert all(not window.isVisible() for window in group.windows)
    assert hidden == [None]

    group.show("Ctrl+Alt+H")
    assert all(window.isVisible() for window in group.windows)
    assert group.visible


def test_screen_retirement_hides_window_even_when_close_is_ignored(
    qtbot: QtBot, monkeypatch
) -> None:
    _patch_fake_screen_windows(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", bounds=app.primaryScreen().geometry())
    second = _FakeScreen("second", bounds=app.primaryScreen().geometry())
    hidden: list[None] = []
    group = CurtainGroup(
        screens=(first, second),  # type: ignore[arg-type]
        on_hidden=lambda: hidden.append(None),
    )
    group.show("")
    retiring_window, surviving_window = group.windows
    qtbot.addWidget(retiring_window)
    qtbot.addWidget(surviving_window)

    events.screenRemoved.emit(first)

    assert group.windows == [surviving_window]
    assert not retiring_window.isVisible()
    assert surviving_window.isVisible()
    assert group.visible
    assert hidden == []


def test_screen_replacement_hides_retired_window_when_close_is_ignored(
    qtbot: QtBot, monkeypatch
) -> None:
    _patch_fake_screen_windows(monkeypatch)
    app = QGuiApplication.instance()
    assert app is not None
    events = _ScreenEvents(app)
    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: events))
    first = _FakeScreen("first", bounds=app.primaryScreen().geometry())
    survivor = _FakeScreen("survivor", bounds=app.primaryScreen().geometry())
    replacement = _FakeScreen("replacement", bounds=app.primaryScreen().geometry())
    current_screens = [first, survivor]
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: tuple(current_screens)))
    hidden: list[None] = []
    group = CurtainGroup(on_hidden=lambda: hidden.append(None))
    group.show("")
    retiring_window, surviving_window = group.windows
    qtbot.addWidget(retiring_window)
    qtbot.addWidget(surviving_window)

    current_screens[:] = [replacement, survivor]
    group.show("")

    assert not retiring_window.isVisible()
    assert group.windows[1] is surviving_window
    assert all(window.isVisible() for window in group.windows)
    assert group.visible
    assert hidden == []
