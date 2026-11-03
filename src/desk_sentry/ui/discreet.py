"""Tiny, non-interactive status cue for discreet monitoring mode."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

from PySide6.QtCore import (
    QObject,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from desk_sentry.sentry import Status
from desk_sentry.ui import theme

CUE_HIDE_DELAY_MS = 2000
NOTICE_HIDE_DELAY_MS = 1000
MARK_FEEDBACK_MS = 400
CUE_DOT_SIZE = 6
CUE_DOT_GAP = 2
CUE_LINE_HEIGHT = 2

_CUES = {"dot", "line", "none"}
_CORNERS = {"top-right", "top-left", "bottom-right", "bottom-left"}
_ACTIVE_STATUSES = {Status.ALERT, Status.APPROACHING, Status.NOTICE}


class _TimeoutSignal(Protocol):
    def connect(self, callback: Callable[[], None]) -> object: ...


class _Timer(Protocol):
    timeout: _TimeoutSignal

    def setSingleShot(self, single: bool) -> None: ...  # noqa: N802 - Qt API shape

    def start(self, delay: int) -> None: ...

    def stop(self) -> None: ...


class _Screen(Protocol):
    def geometry(self) -> QRect: ...


def screen_reference(screen: _Screen, number: int = 0) -> dict[str, str]:
    rect = screen.geometry()
    return {
        "serial": str(getattr(screen, "serialNumber", lambda: "")()),
        "model": str(getattr(screen, "model", lambda: "")()),
        "manufacturer": str(getattr(screen, "manufacturer", lambda: "")()),
        "name": str(getattr(screen, "name", lambda: "")()),
        "geometry": f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}",
        "number": str(number),
    }


def same_screen(a: dict[str, str], b: dict[str, str]) -> bool:
    if a.get("serial"):
        return all(a.get(k, "") == b.get(k, "") for k in ("serial", "model", "manufacturer"))
    return all(a.get(k, "") == b.get(k, "") for k in ("model", "manufacturer", "name", "geometry"))


@dataclass
class ScreenInfo:
    screen: _Screen
    number: int
    primary: bool
    reference: dict[str, str]
    bounds: QRect
    available: QRect


class ScreenCatalog(QObject):
    """One inventory for settings, actual cues and previews; safely reconnect on hotplug."""

    changed = Signal()

    def __init__(
        self,
        screens: Callable[[], list[_Screen]] | None = None,
        primary: Callable[[], _Screen | None] | None = None,
    ) -> None:
        super().__init__()
        self._screens = screens or (lambda: list(QGuiApplication.screens()))
        self._primary = primary or _primary_screen
        self._watched: list[_Screen] = []
        app = QGuiApplication.instance()
        if screens is None and isinstance(app, QGuiApplication):
            app.screenAdded.connect(self.refresh)
            app.screenRemoved.connect(self.refresh)
            app.primaryScreenChanged.connect(self.refresh)
        self._watch()

    def entries(self) -> list[ScreenInfo]:
        primary = self._primary()
        result = []
        for number, screen in enumerate(self._screens(), 1):
            try:
                bounds = screen.geometry()
                available = cast(QRect, getattr(screen, "availableGeometry", screen.geometry)())
                result.append(
                    ScreenInfo(
                        screen,
                        number,
                        screen is primary,
                        screen_reference(screen, number),
                        bounds,
                        available,
                    )
                )
            except RuntimeError:
                continue  # QScreen can already be destroyed inside screenRemoved.
        return result

    def selected(
        self, mode: str, references: tuple[dict[str, str], ...]
    ) -> tuple[list[ScreenInfo], bool]:
        entries = self.entries()
        if mode == "all":
            return entries, False
        primary = [e for e in entries if e.primary] or entries[:1]
        if mode == "primary":
            return primary, False
        selected = [e for e in entries if any(same_screen(r, e.reference) for r in references)]
        return (selected, False) if selected else (primary, bool(primary))

    def _watch(self) -> None:
        for screen in self._watched:
            for name in ("geometryChanged", "availableGeometryChanged"):
                signal = getattr(screen, name, None)
                if signal is not None:
                    with suppress(RuntimeError, TypeError):
                        signal.disconnect(self.refresh)
        self._watched = self._screens()
        for screen in self._watched:
            for name in ("geometryChanged", "availableGeometryChanged"):
                signal = getattr(screen, name, None)
                if signal is not None:
                    with suppress(RuntimeError):
                        signal.connect(self.refresh)

    def refresh(self, *_args: object) -> None:
        self._watch()
        self.changed.emit()


def screen_rectangles(entries: list[ScreenInfo], area: QRectF) -> list[QRectF]:
    if not entries:
        return []
    union = QRect(entries[0].bounds)
    for entry in entries[1:]:
        union = union.united(entry.bounds)
    scale = min(
        (area.width() - 16) / max(1, union.width()), (area.height() - 16) / max(1, union.height())
    )
    # Shrink every monitor by the SAME factor around its scaled centre: preserve
    # aspect ratios and relative arrangement, leaving >=4px between touching edges.
    smallest = min(min(e.bounds.width(), e.bounds.height()) * scale for e in entries)
    inset_scale = max(0.1, 1 - 4 / max(1, smallest))
    result = []
    for entry in entries:
        b = entry.bounds
        center_x = area.center().x() + (b.center().x() - union.center().x()) * scale
        center_y = area.center().y() + (b.center().y() - union.center().y()) * scale
        w, h = b.width() * scale * inset_scale, b.height() * scale * inset_scale
        result.append(QRectF(center_x - w / 2, center_y - h / 2, w, h))
    return result


class ScreenMap(QWidget):
    screen_clicked = Signal(int)

    def __init__(self, catalog: ScreenCatalog) -> None:
        super().__init__()
        self.catalog = catalog
        self.mode = "all"
        self.references: tuple[dict[str, str], ...] = ()
        self.cue = "dot"
        self.corner = "top-right"
        self.setFixedHeight(96)
        self.setMouseTracking(True)
        self.setAccessibleName("顯示螢幕配置圖")
        self._shake_index = -1
        self._offset = 0.0
        self._shake = QVariantAnimation(self)
        self._shake.setDuration(120)
        self._shake.setStartValue(0.0)
        self._shake.setEndValue(1.0)
        self._shake.valueChanged.connect(self._shake_frame)
        catalog.changed.connect(self.update)

    def configure(
        self, mode: str, references: tuple[dict[str, str], ...], cue: str, corner: str
    ) -> None:
        self.mode, self.references, self.cue, self.corner = mode, references, cue, corner
        self.update()

    def rectangles(self) -> list[QRectF]:
        return screen_rectangles(self.catalog.entries(), QRectF(self.rect()))

    def reject(self, index: int) -> None:
        self._shake_index = index
        self._shake.start()

    def _shake_frame(self, value: object) -> None:
        self._offset = math.sin(float(cast(float, value)) * 4 * math.pi) * 2
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            for index, rect in enumerate(self.rectangles()):
                if rect.contains(event.position()):
                    self.screen_clicked.emit(index)
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        for entry, rect in zip(self.catalog.entries(), self.rectangles(), strict=True):
            if rect.contains(event.position()):
                self.setToolTip(
                    f"{entry.number} · {entry.reference['manufacturer']} "
                    f"{entry.reference['model']} · "
                    f"{entry.bounds.width()}×{entry.bounds.height()}"
                )
                return
        self.setToolTip("")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(QColor(theme.SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        selected, _fallback = self.catalog.selected(self.mode, self.references)
        selected_ids = {id(e.screen) for e in selected}
        for index, (entry, rect) in enumerate(
            zip(self.catalog.entries(), self.rectangles(), strict=True)
        ):
            rect = QRectF(rect)
            if index == self._shake_index:
                rect.translate(self._offset, 0)
            active = id(entry.screen) in selected_ids
            painter.setBrush(QColor(theme.RAISED if active else theme.SURFACE))
            painter.setPen(QColor(theme.ACCENT if active else theme.TEXT_3))
            painter.drawRoundedRect(rect, 3, 3)
            font = QFont(self.font())
            font.setPixelSize(theme.FONT_SIZES["body"])
            painter.setFont(font)
            title = QRectF(rect)
            if entry.primary:
                title.adjust(0, -6, 0, -6)
            painter.drawText(title, Qt.AlignmentFlag.AlignCenter, str(entry.number))
            if entry.primary:
                font.setPixelSize(theme.FONT_SIZES["caption"])
                painter.setFont(font)
                painter.drawText(rect.adjusted(0, 18, 0, 0), Qt.AlignmentFlag.AlignCenter, "主要")
            if active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.NOTICE))
                if self.cue == "line":
                    painter.drawRect(QRectF(rect.x() + 3, rect.y() + 2, rect.width() - 6, 2))
                elif self.cue == "dot":
                    x = rect.left() + 5 if self.corner.endswith("left") else rect.right() - 9
                    y = rect.top() + 5 if self.corner.startswith("top") else rect.bottom() - 9
                    painter.drawEllipse(QRectF(x, y, 4, 4))
        painter.end()


def _primary_screen() -> _Screen | None:
    return QGuiApplication.primaryScreen()


def _set_widget_screen(window: QWidget, screen: _Screen) -> None:
    if isinstance(screen, QScreen):
        window.setScreen(screen)


class _CueWindow(QWidget):
    """Input-transparent cue surface; all timing is owned by DiscreetCue."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.color = ""
        self.cue = "dot"
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")

    def set_appearance(self, cue: str, color: str) -> None:
        self.cue = cue
        self.color = color
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, self.cue == "dot")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        if self.cue == "dot":
            painter.drawEllipse(QRectF(self.rect()))
        else:
            painter.drawRect(self.rect())
        painter.end()


class DiscreetCue(QObject):
    """Show a dot or line during alert states, then linger briefly after quiet."""

    def __init__(
        self,
        *,
        cue: str = "dot",
        corner: str = "top-right",
        clock: Callable[[], float] = monotonic,
        timer: _Timer | None = None,
        feedback_timer: _Timer | None = None,
        screen_provider: Callable[[], _Screen | None] | None = None,
        screens_provider: Callable[[], list[_Screen]] | None = None,
        catalog: ScreenCatalog | None = None,
        on_fallback: Callable[[], None] | None = None,
        screen_setter: Callable[[QWidget, _Screen], None] = _set_widget_screen,
    ) -> None:
        super().__init__()
        self._cue = cue if cue in _CUES else "none"
        self._corner = corner if corner in _CORNERS else "top-right"
        self._clock = clock
        self._timer = timer or cast(_Timer, QTimer(self))
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._feedback_timer = feedback_timer or cast(_Timer, QTimer(self))
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._finish_feedback)
        if catalog is None:
            if screens_provider is None and screen_provider is not None:

                def single() -> list[_Screen]:
                    screen = screen_provider()
                    return [screen] if screen is not None else []

                screens_provider = single
            catalog = ScreenCatalog(screens_provider, screen_provider)
            catalog.setParent(self)
        self.catalog = catalog
        self.catalog.changed.connect(self._screens_changed)
        self._mode = "all"
        self._references: tuple[dict[str, str], ...] = ()
        self._on_fallback = on_fallback or (lambda: None)
        self._fallback_active = False
        self._screen_setter = screen_setter
        self._hide_deadline: float | None = None
        self._feedback_deadline: float | None = None
        self._signal_active = False
        self._last_active_status = Status.ALERT
        self._visible = False
        self._color = theme.STATUS_COLOR[Status.ALERT]
        self.window: _CueWindow | None = None
        self._windows: dict[int, _CueWindow] = {}
        self._preview_windows: list[_CueWindow] = []
        self.identifiers: list[ScreenIdentifier] = []
        self._preview_identify = False
        self._preview_deadline: float | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._finish_preview)
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_identifiers)

    @property
    def windows(self) -> list[_CueWindow]:
        return list(self._windows.values())

    @property
    def visible(self) -> bool:
        return self._visible

    def configure(
        self, cue: str, corner: str, mode: str = "all", references: tuple[dict[str, str], ...] = ()
    ) -> None:
        self._cue = cue if cue in _CUES else "none"
        self._corner = corner if corner in _CORNERS else "top-right"
        self._mode, self._references = mode, references
        if self._cue == "none":
            self._discard_window()
            return
        if self._visible and self._sync_window():
            self._show_windows()

    def update_status(self, status: Status) -> None:
        if self._cue == "none":
            self._discard_window()
            return
        if status in _ACTIVE_STATUSES:
            self._signal_active = True
            self._last_active_status = status
            self._hide_deadline = None
            self._timer.stop()
            self._color = theme.STATUS_COLOR[status]
            if not self._sync_window():
                return
            self._show_windows()
            self._visible = True
            return
        if self._signal_active and self._hide_deadline is None:
            delay = (
                NOTICE_HIDE_DELAY_MS
                if self._last_active_status is Status.NOTICE
                else CUE_HIDE_DELAY_MS
            )
            self._hide_deadline = self._clock() + delay / 1000
            self._timer.start(delay)

    def pulse_feedback(self) -> None:
        if self._cue == "none":
            return
        self._feedback_deadline = self._clock() + MARK_FEEDBACK_MS / 1000
        self._feedback_timer.start(MARK_FEEDBACK_MS)
        if self._sync_window():
            self._show_windows()
            self._visible = True

    def _finish_feedback(self) -> None:
        if self._feedback_deadline is None:
            return
        remaining = self._feedback_deadline - self._clock()
        if remaining > 0:
            self._feedback_timer.start(max(1, math.ceil(remaining * 1000)))
            return
        self._feedback_deadline = None
        if self._hide_deadline is not None and self._clock() >= self._hide_deadline:
            self._on_timeout()
        if self._signal_active:
            self._sync_window()
        else:
            self.hide()

    def hide(self) -> None:
        self._timer.stop()
        self._feedback_timer.stop()
        self._finish_preview()
        self._hide_deadline = None
        self._feedback_deadline = None
        self._signal_active = False
        for window in self.windows:
            window.hide()
        self._visible = False

    def close(self) -> None:
        self._discard_window()
        self._finish_preview()

    def _show_windows(self) -> None:
        for window in self.windows:
            window.show()
            window.raise_()

    def _sync_window(self) -> bool:
        selected, fallback = self.catalog.selected(self._mode, self._references)
        if fallback and not self._fallback_active:
            self._on_fallback()
        self._fallback_active = fallback
        live = {id(e.screen) for e in selected}
        for key in list(self._windows):
            if key not in live:
                removed = self._windows.pop(key)
                removed.close()
                removed.deleteLater()
        if not selected:
            self.window = None
            self.hide()
            return False
        feedback = self._feedback_deadline is not None
        cue = "dot" if feedback else self._cue
        color = theme.STATUS_COLOR[Status.PAUSED] if feedback else self._color
        for entry in selected:
            window = self._windows.get(id(entry.screen))
            if window is None:
                window = self._windows[id(entry.screen)] = _CueWindow()
            self._screen_setter(window, entry.screen)
            window.set_appearance(cue, color)
            window.setGeometry(self._geometry(entry.available, cue))
        self.window = self.windows[0]
        return True

    def _screens_changed(self) -> None:
        if self._visible and self._sync_window():
            self._show_windows()
        if self._preview_deadline is not None:
            remaining = self._preview_deadline - self._clock()
            if remaining > 0:
                self.preview(round(remaining * 1000), identify=self._preview_identify)

    def preview(self, duration_ms: int = 1500, *, identify: bool = False) -> None:
        self._finish_preview()
        self._preview_identify = identify
        self._preview_deadline = self._clock() + duration_ms / 1000
        selected, _fallback = self.catalog.selected(self._mode, self._references)
        if self._cue != "none":
            for entry in selected:
                window = _CueWindow()
                self._screen_setter(window, entry.screen)
                window.set_appearance(self._cue, theme.STATUS_COLOR[Status.PAUSED])
                window.setGeometry(self._geometry(entry.available, self._cue))
                window.show()
                self._preview_windows.append(window)
        if identify:
            for entry in self.catalog.entries():
                panel = ScreenIdentifier(entry)
                self._screen_setter(panel, entry.screen)
                panel.show()
                panel.fade(True)
                self.identifiers.append(panel)
            self._fade_timer.start(max(1, duration_ms - 150))
        self._preview_timer.start(duration_ms)

    def _fade_identifiers(self) -> None:
        for panel in self.identifiers:
            panel.fade(False)

    def _finish_preview(self) -> None:
        self._preview_timer.stop()
        self._fade_timer.stop()
        self._preview_deadline = None
        for window in [*self._preview_windows, *self.identifiers]:
            window.close()
            window.deleteLater()
        self._preview_windows.clear()
        self.identifiers.clear()

    def _geometry(self, screen: QRect, cue: str) -> QRect:
        if cue == "line":
            return QRect(screen.x(), screen.y(), screen.width(), CUE_LINE_HEIGHT)
        x = (
            screen.x() + CUE_DOT_GAP
            if self._corner.endswith("left")
            else screen.right() - CUE_DOT_GAP - CUE_DOT_SIZE + 1
        )
        y = (
            screen.y() + CUE_DOT_GAP
            if self._corner.startswith("top")
            else screen.bottom() - CUE_DOT_GAP - CUE_DOT_SIZE + 1
        )
        return QRect(x, y, CUE_DOT_SIZE, CUE_DOT_SIZE)

    def _on_timeout(self) -> None:
        if self._hide_deadline is None:
            return
        remaining = self._hide_deadline - self._clock()
        if remaining > 0:
            self._timer.start(max(1, math.ceil(remaining * 1000)))
            return
        self._signal_active = False
        self._hide_deadline = None
        if self._feedback_deadline is None:
            self.hide()

    def _discard_window(self) -> None:
        self.hide()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self._windows.clear()
        self.window = None


class ScreenIdentifier(_CueWindow):
    def __init__(self, entry: ScreenInfo) -> None:
        super().__init__()
        self.entry = entry
        self.resize(160, 160)
        self.move(entry.bounds.center() - self.rect().center())
        self._animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._animation.setDuration(150)

    def fade(self, visible: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self.windowOpacity() if not visible else 0.0)
        self._animation.setEndValue(1.0 if visible else 0.0)
        self._animation.start()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(14, 17, 22, 225))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setPen(QColor(theme.TEXT))
        font = QFont(self.font())
        font.setPixelSize(72)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 10, 160, 105), Qt.AlignmentFlag.AlignCenter, str(self.entry.number)
        )
        font.setPixelSize(12)
        painter.setFont(font)
        prefix = "主要 · " if self.entry.primary else ""
        painter.drawText(
            QRectF(0, 115, 160, 28),
            Qt.AlignmentFlag.AlignCenter,
            f"{prefix}{self.entry.bounds.width()}×{self.entry.bounds.height()}",
        )
        painter.end()
