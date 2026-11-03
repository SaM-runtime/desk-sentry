"""Full-screen privacy curtain displayed across every monitor."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QScreen,
    QShortcut,
)
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from desk_sentry.ui import motion, theme


class _CurtainWindow(QWidget):
    """One curtain surface bound to one screen."""

    def __init__(self, dismiss: Callable[[], None]) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._dismiss = dismiss
        self.setObjectName("privacyCurtain")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(f"background: {theme.STAGE};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.D["space_24"], theme.D["space_24"], theme.D["space_24"], theme.D["space_24"]
        )
        layout.setSpacing(theme.D["space_8"])
        layout.addStretch(1)
        self.title = QLabel("Desk Sentry")
        self.title.setObjectName("curtainTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet(
            f"color: {theme.TEXT}; font-size: {theme.FONT_SIZES['title']}px; font-weight: 600;"
        )
        layout.addWidget(self.title)
        self.instruction = QLabel()
        self.instruction.setObjectName("curtainInstruction")
        self.instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.instruction.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        layout.addWidget(self.instruction)
        layout.addStretch(1)

    def set_hotkey(self, hotkey: str) -> None:
        self.instruction.setText(
            f"按 {hotkey} 或連按兩下移開" if hotkey else "連按兩下或按 Esc 移開"
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self._dismiss()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._dismiss()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        event.ignore()


class CurtainGroup(QObject):
    """Coordinate screen curtains and keep the mini mirror above them."""

    def __init__(
        self,
        mirror: QWidget | None = None,
        screens: Iterable[QScreen] | None = None,
        on_hidden: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._mirror = mirror
        self._screens = tuple(screens) if screens is not None else None
        self.on_hidden = on_hidden
        self.windows: list[_CurtainWindow] = []
        self._animations: list[QPropertyAnimation] = []
        self._shortcuts: list[QShortcut] = []
        self._visible = False
        self._hotkey = ""
        self._discreet = False
        self._window_screens: list[QScreen] = []
        self._screen_signal_source: QGuiApplication | None = None

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self, hotkey: str) -> None:
        screens = self._screens if self._screens is not None else tuple(QGuiApplication.screens())
        self._stop_animations()
        self._sync_windows(screens)
        self._hotkey = hotkey
        self._install_shortcuts(hotkey)
        duration = theme.MOTION["hover"]
        animate = motion.animations_enabled()
        for window, screen in zip(self.windows, screens, strict=True):
            self._show_window(window, screen, hotkey, animate=animate, duration=duration)
        self._visible = True
        self._connect_screen_signals()
        self._raise_mirror()

    def hide(self) -> None:
        was_visible = self._visible
        self._disconnect_screen_signals()
        self._stop_animations()
        for window in self.windows:
            window.hide()
        self._visible = False
        if was_visible and self.on_hidden is not None:
            self.on_hidden()

    def toggle(self, hotkey: str) -> None:
        if self.visible:
            self.hide()
        else:
            self.show(hotkey)

    def set_discreet(self, discreet: bool) -> None:
        self._discreet = discreet
        for window in self.windows:
            self._apply_discreet(window)

    def _sync_windows(self, screens: tuple[QScreen, ...]) -> None:
        remaining = list(zip(self._window_screens, self.windows, strict=True))
        synced_windows: list[_CurtainWindow] = []
        for screen in screens:
            match = next(
                (
                    (index, window)
                    for index, (current, window) in enumerate(remaining)
                    if current is screen
                ),
                None,
            )
            if match is None:
                window = _CurtainWindow(self.hide)
                self._apply_discreet(window)
                synced_windows.append(window)
                continue
            index, window = match
            remaining.pop(index)
            synced_windows.append(window)
        for _screen, window in remaining:
            window.hide()
            window.close()
            window.deleteLater()
        self.windows = synced_windows
        self._window_screens = list(screens)

    def _show_window(
        self,
        window: _CurtainWindow,
        screen: QScreen,
        hotkey: str,
        *,
        animate: bool,
        duration: int,
    ) -> None:
        window.set_hotkey(hotkey)
        window.setScreen(screen)
        window.setGeometry(screen.geometry())
        window.setWindowOpacity(0.0 if animate else 1.0)
        window.showFullScreen()
        window.raise_()
        window.activateWindow()
        if animate:
            animation = QPropertyAnimation(window, b"windowOpacity", window)
            animation.setDuration(duration)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.finished.connect(self._raise_mirror)
            self._animations.append(animation)
            animation.start()

    def _connect_screen_signals(self) -> None:
        if self._screen_signal_source is not None:
            return
        app = cast(QGuiApplication | None, QGuiApplication.instance())
        if app is None:
            return
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        self._screen_signal_source = app

    def _disconnect_screen_signals(self) -> None:
        app = self._screen_signal_source
        if app is None:
            return
        app.screenAdded.disconnect(self._on_screen_added)
        app.screenRemoved.disconnect(self._on_screen_removed)
        self._screen_signal_source = None

    def _on_screen_added(self, screen: QScreen) -> None:
        if not self._visible or any(current is screen for current in self._window_screens):
            return
        if self._screens is not None:
            self._screens = (*self._screens, screen)
        window = _CurtainWindow(self.hide)
        self._apply_discreet(window)
        self.windows.append(window)
        self._window_screens.append(screen)
        if len(self.windows) == 1:
            self._install_shortcuts(self._hotkey)
        self._show_window(
            window,
            screen,
            self._hotkey,
            animate=motion.animations_enabled(),
            duration=theme.MOTION["hover"],
        )
        self._raise_mirror()

    def _on_screen_removed(self, screen: QScreen) -> None:
        """Close the curtain for a screen before Qt moves it to the primary display."""
        match = next(
            (index for index, current in enumerate(self._window_screens) if current is screen),
            None,
        )
        if not self._visible or match is None:
            return
        window = self.windows.pop(match)
        self._window_screens.pop(match)
        if self._screens is not None:
            self._screens = tuple(current for current in self._screens if current is not screen)
        for animation in tuple(self._animations):
            if animation.targetObject() is window:
                animation.stop()
                animation.deleteLater()
                self._animations.remove(animation)
        window.hide()
        window.close()
        window.deleteLater()
        self._install_shortcuts(self._hotkey)
        self._raise_mirror()

    def _stop_animations(self) -> None:
        for animation in self._animations:
            animation.stop()
            animation.deleteLater()
        self._animations.clear()

    def _apply_discreet(self, window: _CurtainWindow) -> None:
        window.title.setVisible(not self._discreet)
        window.instruction.setVisible(not self._discreet)

    def _install_shortcuts(self, hotkey: str) -> None:
        for shortcut in self._shortcuts:
            shortcut.deleteLater()
        self._shortcuts = []
        if not hotkey:
            return
        if self.windows:
            shortcut = QShortcut(QKeySequence(hotkey), self.windows[0])
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(self.hide)
            self._shortcuts.append(shortcut)

    def _raise_mirror(self) -> None:
        if self._mirror is not None and self._mirror.isVisible():
            self._mirror.raise_()
