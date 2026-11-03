"""Task 06 low-profile alert cue behavior."""

from collections.abc import Callable

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.discreet import CUE_HIDE_DELAY_MS, DiscreetCue, _CueWindow


class _Signal:
    def __init__(self) -> None:
        self.callback: Callable[[], None] | None = None

    def connect(self, callback: Callable[[], None]) -> None:
        self.callback = callback


class _Timer:
    def __init__(self) -> None:
        self.timeout = _Signal()
        self.started: list[int] = []
        self.stopped = 0

    def setSingleShot(self, _single: bool) -> None:  # noqa: N802 - Qt API shape
        return None

    def start(self, delay: int) -> None:
        self.started.append(delay)

    def stop(self) -> None:
        self.stopped += 1

    def fire(self) -> None:
        assert self.timeout.callback is not None
        self.timeout.callback()


class _Screen:
    def __init__(self, geometry: QRect) -> None:
        self._geometry = geometry

    def geometry(self) -> QRect:
        return QRect(self._geometry)


@pytest.fixture
def no_real_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_CueWindow, "show", lambda _self: None)
    monkeypatch.setattr(_CueWindow, "raise_", lambda _self: None)


def _cue(
    *,
    cue: str = "dot",
    corner: str = "bottom-right",
    now: list[float] | None = None,
) -> tuple[DiscreetCue, _Timer]:
    clock = now if now is not None else [0.0]
    timer = _Timer()
    screen = _Screen(QRect(100, 200, 800, 600))
    result = DiscreetCue(
        cue=cue,
        corner=corner,
        clock=lambda: clock[0],
        timer=timer,
        screen_provider=lambda: screen,  # type: ignore[arg-type]
        screen_setter=lambda _window, _screen: None,
    )
    return result, timer


@pytest.mark.parametrize(
    ("corner", "expected"),
    [
        ("top-left", QRect(102, 202, 6, 6)),
        ("top-right", QRect(892, 202, 6, 6)),
        ("bottom-left", QRect(102, 792, 6, 6)),
        ("bottom-right", QRect(892, 792, 6, 6)),
    ],
)
def test_dot_geometry_flags_and_alert_color(
    qtbot: QtBot,
    no_real_window: None,
    corner: str,
    expected: QRect,
) -> None:
    cue, _timer = _cue(corner=corner)
    cue.update_status(Status.ALERT)
    assert cue.window is not None
    qtbot.addWidget(cue.window)

    assert cue.visible
    assert cue.window.geometry() == expected
    assert cue.window.color == theme.STATUS_COLOR[Status.ALERT]
    assert cue.window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert cue.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert cue.window.windowFlags() & Qt.WindowType.Tool
    assert cue.window.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert cue.window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert cue.window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert cue.window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_line_geometry_and_approaching_color(qtbot: QtBot, no_real_window: None) -> None:
    cue, _timer = _cue(cue="line")
    cue.update_status(Status.APPROACHING)
    assert cue.window is not None
    qtbot.addWidget(cue.window)

    assert cue.window.geometry() == QRect(100, 200, 800, 2)
    assert cue.window.color == theme.STATUS_COLOR[Status.APPROACHING]


def test_dot_pixels_have_transparent_corners_and_opaque_center(
    qtbot: QtBot, no_real_window: None
) -> None:
    app = QApplication.instance()
    assert app is not None
    previous = app.styleSheet()
    app.setStyleSheet(theme.STYLESHEET)
    try:
        cue, _timer = _cue()
        cue.update_status(Status.ALERT)
        assert cue.window is not None
        qtbot.addWidget(cue.window)
        image = cue.window.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)

        assert image.pixelColor(0, 0).alpha() < 255
        assert image.pixelColor(3, 3).alpha() == 255
    finally:
        app.setStyleSheet(previous)


def test_quiet_linger_renewal_and_deadline(qtbot: QtBot, no_real_window: None) -> None:
    now = [10.0]
    cue, timer = _cue(now=now)
    cue.update_status(Status.ALERT)
    assert cue.window is not None
    qtbot.addWidget(cue.window)

    cue.update_status(Status.CLEAR)
    assert timer.started[-1] == CUE_HIDE_DELAY_MS
    now[0] = 11.9
    timer.fire()
    assert cue.visible

    cue.update_status(Status.ALERT)
    cue.update_status(Status.WATCHING)
    now[0] = 13.8
    timer.fire()
    assert cue.visible
    now[0] = 13.9
    timer.fire()
    assert not cue.visible


def test_repeated_quiet_frames_do_not_reset_deadline(qtbot: QtBot, no_real_window: None) -> None:
    now = [0.0]
    cue, timer = _cue(now=now)
    cue.update_status(Status.ALERT)
    assert cue.window is not None
    qtbot.addWidget(cue.window)
    cue.update_status(Status.CLEAR)
    now[0] = 1.0
    cue.update_status(Status.WATCHING)
    assert timer.started == [CUE_HIDE_DELAY_MS]
    now[0] = 2.0
    timer.fire()
    assert not cue.visible


def test_none_is_lazy_and_reconfiguration_is_safe(no_real_window: None) -> None:
    cue, _timer = _cue(cue="none")
    cue.update_status(Status.ALERT)
    assert cue.window is None
    assert not cue.visible

    cue.configure("dot", "top-left")
    cue.update_status(Status.ALERT)
    assert cue.window is not None
    assert cue.window.geometry() == QRect(102, 202, 6, 6)
    cue.configure("line", "bottom-right")
    assert cue.window.geometry() == QRect(100, 200, 800, 2)
    cue.configure("none", "bottom-right")
    assert cue.window is None
    assert not cue.visible


def test_missing_primary_screen_does_not_show(qtbot: QtBot, no_real_window: None) -> None:
    cue = DiscreetCue(screen_provider=lambda: None)
    cue.update_status(Status.ALERT)
    if cue.window is not None:
        qtbot.addWidget(cue.window)

    assert not cue.visible


def test_active_status_tracks_primary_screen_change(qtbot: QtBot, no_real_window: None) -> None:
    screens = [_Screen(QRect(0, 0, 800, 600))]
    cue = DiscreetCue(
        screen_provider=lambda: screens[0],  # type: ignore[arg-type]
        screen_setter=lambda _window, _screen: None,
    )
    cue.update_status(Status.ALERT)
    assert cue.window is not None
    qtbot.addWidget(cue.window)
    assert cue.window.geometry() == QRect(792, 2, 6, 6)

    screens[0] = _Screen(QRect(800, 0, 1024, 768))
    cue.update_status(Status.ALERT)
    assert cue.window.geometry() == QRect(1816, 2, 6, 6)
