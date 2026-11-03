"""Mini mirror mode: only a small, resizable, always-on-top camera window."""

from __future__ import annotations

from collections.abc import Callable
from ctypes import wintypes
from typing import cast, overload

from PySide6.QtCore import (
    Property,
    QByteArray,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QScreen,
    QShowEvent,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QMenu, QVBoxLayout, QWidget

from desk_sentry.engine import Snapshot
from desk_sentry.ui import motion, theme
from desk_sentry.ui.controls import IconButton
from desk_sentry.ui.mirror_view import MirrorView

EDGE = theme.D["mini_resize_edge"]
DEFAULT_SIZE = (theme.D["mini_width"], theme.D["mini_height"])
MIN_SIZE = (theme.D["mini_min_width"], theme.D["mini_min_height"])
_MARGIN = theme.D["mini_screen_margin"]
GEOMETRY_SAVE_DELAY_MS = 300
_WM_ENTERSIZEMOVE = 0x0231
_WM_EXITSIZEMOVE = 0x0232

Geometry = tuple[int, int, int, int]


def _height_for_width(width: int) -> int:
    return round(width * 9 / 16)


class _HoverShade(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.opacity = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        top = QColor(theme.MINI_HOVER_TOP)
        top.setAlphaF(top.alphaF() * self.opacity)
        bottom = QColor(top)
        bottom.setAlpha(0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        radius = theme.D["mini_view_radius"]
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        p.end()


class MiniMirror(QWidget):
    def __init__(self, *, screen_source: QGuiApplication | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Desk Sentry 小鏡子")
        self.setMouseTracking(True)
        self.setMinimumSize(*MIN_SIZE)
        self._correcting_aspect = False
        self._native_resize_edges = Qt.Edge(0)
        self._geometry_dragging = False
        self._geometry_dirty = False
        self.view = MirrorView(
            radius=theme.D["mini_radius"],
            compact=True,
            pickable=False,
            badge=True,
            align_top=False,
            border_color=theme.MINI_BORDER,
        )
        self.view.setMinimumSize(*MIN_SIZE)
        self.view.setToolTip("拖曳移動 · 拖曳邊緣縮放 · 雙擊開啟設定 · 右鍵選單")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self._hover_opacity = 0.0
        self._hover_shade = _HoverShade(self)
        self._hover_animation = QPropertyAnimation(self, b"hoverOpacity", self)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._hover_animation.finished.connect(self._hover_animation_finished)
        hover_button = theme.D["mini_hover_button"]
        self._expand_button = IconButton("settings", "開啟設定", size=hover_button)
        self._expand_button.setParent(self)
        self._expand_button.clicked.connect(self._expand)
        self._close_button = IconButton("close", "隱藏小鏡子", size=hover_button)
        self._close_button.setParent(self)
        self._close_button.clicked.connect(self._close)
        self._button_effects: list[QGraphicsOpacityEffect] = []
        for button in (self._expand_button, self._close_button):
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(0.0)
            button.setGraphicsEffect(effect)
            button.hide()
            self._button_effects.append(effect)

        self.on_expand: Callable[[], None] | None = None
        self.on_toggle_armed: Callable[[], None] | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_history: Callable[[], None] | None = None
        self.history_count = 0
        self.on_geometry: Callable[[Geometry], None] | None = None
        self.armed = True
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(GEOMETRY_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._emit_geometry)
        self.resize(*DEFAULT_SIZE)
        source = screen_source or cast(QGuiApplication | None, QGuiApplication.instance())
        if source is not None:
            source.screenAdded.connect(self._screen_added)
            source.screenRemoved.connect(self._screen_removed)

    # ------------------------------------------------------------- placement
    def restore(self, geometry: Geometry | None) -> None:
        self._native_resize_edges = Qt.Edge(0)
        rect = None
        if geometry is not None:
            x, y, saved_width, _saved_height = geometry
            width = max(MIN_SIZE[0], saved_width)
            rect = QRect(x, y, width, _height_for_width(width))
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        if rect is None or not any(s.intersects(rect) for s in screens):
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
            w, h = DEFAULT_SIZE
            rect = QRect(area.right() - w - _MARGIN, area.bottom() - h - _MARGIN, w, h)
        self.setGeometry(rect)

    def _emit_geometry(self) -> None:
        if self.on_geometry is not None and self.isVisible() and self._geometry_dirty:
            g = self.geometry()
            self.on_geometry((g.x(), g.y(), g.width(), g.height()))
            self._geometry_dirty = False

    def ensure_on_screen(self, exclude: QScreen | None = None) -> None:
        screens = [screen for screen in QGuiApplication.screens() if screen is not exclude]
        if any(screen.availableGeometry().intersects(self.geometry()) for screen in screens):
            return
        primary: QScreen | None = QGuiApplication.primaryScreen()
        if primary is exclude:
            primary = screens[0] if screens else None
        area = primary.availableGeometry() if primary is not None else QRect(0, 0, 1280, 720)
        width, height = DEFAULT_SIZE
        self.setGeometry(
            area.right() - width - _MARGIN, area.bottom() - height - _MARGIN, width, height
        )

    def _screen_added(self, _screen: QScreen) -> None:
        self.ensure_on_screen()

    def _screen_removed(self, screen: QScreen) -> None:
        self.ensure_on_screen(exclude=screen)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        self.ensure_on_screen()
        super().showEvent(event)

    def _set_geometry_dragging(self, dragging: bool) -> None:
        self._geometry_dragging = dragging
        self._save_timer.stop()
        if not dragging and self._geometry_dirty:
            self._save_timer.start()

    def _schedule_geometry_save(self) -> None:
        self._geometry_dirty = True
        if not self._geometry_dragging:
            self._save_timer.start()

    def nativeEvent(  # noqa: N802 - Qt API
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int, /
    ) -> object:
        if message and event_type == b"windows_generic_MSG":
            packet = wintypes.MSG.from_address(int(message))
            if packet.message == _WM_ENTERSIZEMOVE:
                self._set_geometry_dragging(True)
            elif packet.message == _WM_EXITSIZEMOVE:
                self._set_geometry_dragging(False)
        # QWidget's default is unhandled; let Qt continue its normal native dispatch.
        return False, 0

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802 - Qt API
        self._schedule_geometry_save()
        super().moveEvent(event)

    @overload
    def resize(self, size: QSize, /) -> None: ...

    @overload
    def resize(self, width: int, height: int, /) -> None: ...

    def resize(self, width: int | QSize, height: int | None = None, /) -> None:
        self._native_resize_edges = Qt.Edge(0)
        requested_width = width.width() if isinstance(width, QSize) else width
        normalized_width = max(MIN_SIZE[0], requested_width)
        super().resize(normalized_width, _height_for_width(normalized_width))

    def _aspect_geometry(self, rect: QRect, edges: Qt.Edge) -> QRect:
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if vertical and not horizontal:
            height = max(MIN_SIZE[1], rect.height())
            width = max(MIN_SIZE[0], round(height * 16 / 9))
            center_x = rect.center().x()
            x = center_x - (width - 1) // 2
            return QRect(x, rect.y(), width, height)

        width = max(MIN_SIZE[0], rect.width())
        height = _height_for_width(width)
        if edges & Qt.Edge.TopEdge:
            y = rect.bottom() - height + 1
        elif horizontal and not vertical:
            center_y = rect.center().y()
            y = center_y - (height - 1) // 2
        else:
            y = rect.y()
        return QRect(rect.x(), y, width, height)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        corrected = self._aspect_geometry(self.geometry(), self._native_resize_edges)
        if not self._correcting_aspect and self.geometry() != corrected:
            self._correcting_aspect = True
            try:
                super().setGeometry(corrected)
            finally:
                self._correcting_aspect = False
        self._schedule_geometry_save()
        button = theme.D["mini_hover_button"]
        right = self.width() - theme.D["mini_hover_right"]
        top = theme.D["mini_hover_top"]
        self._hover_shade.setGeometry(
            0,
            0,
            self.width(),
            theme.D["mini_hover_height"],
        )
        self._close_button.setGeometry(right - button, top, button, button)
        self._expand_button.setGeometry(
            right - 2 * button - theme.D["mini_hover_gap"],
            top,
            button,
            button,
        )
        super().resizeEvent(event)

    # ----------------------------------------------------------------- state
    def set_snapshot(self, snap: Snapshot) -> None:
        self.view.set_snapshot(snap)

    def _get_hover_opacity(self) -> float:
        return self._hover_opacity

    def _set_hover_opacity(self, value: float) -> None:
        self._hover_opacity = value
        visible = value > 0.001
        self._hover_shade.opacity = value
        self._hover_shade.setVisible(visible)
        self._hover_shade.update()
        for button, effect in zip(
            (self._expand_button, self._close_button), self._button_effects, strict=True
        ):
            button.setVisible(visible)
            effect.setOpacity(value)
        self.update()

    hoverOpacity = Property(float, _get_hover_opacity, _set_hover_opacity)  # noqa: N815

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt API
        self._animate_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self._animate_hover(False)
        super().leaveEvent(event)

    def _animate_hover(self, visible: bool) -> None:
        self._hover_animation.stop()
        target = 1.0 if visible else 0.0
        if not motion.animations_enabled():
            self._set_hover_opacity(target)
            return
        self._hover_animation.setStartValue(self._hover_opacity)
        self._hover_animation.setEndValue(target)
        self._hover_animation.setDuration(
            theme.MOTION["fade_in"] if visible else theme.MOTION["fade_out"]
        )
        self._hover_animation.start()

    def _hover_animation_finished(self) -> None:
        if self._hover_opacity <= 0.001:
            self._expand_button.hide()
            self._close_button.hide()

    def _expand(self) -> None:
        if self.on_expand is not None:
            self.on_expand()

    def _close(self) -> None:
        if self.on_close is not None:
            self.on_close()

    # ----------------------------------------------------------- interaction
    def _edges(self, pos: QPoint) -> Qt.Edge:
        edges = Qt.Edge(0)
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        edges = self._edges(event.position().toPoint())
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            main_diagonal = edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            )
            shape = (
                Qt.CursorShape.SizeFDiagCursor if main_diagonal else Qt.CursorShape.SizeBDiagCursor
            )
        elif horizontal:
            shape = Qt.CursorShape.SizeHorCursor
        elif vertical:
            shape = Qt.CursorShape.SizeVerCursor
        else:
            shape = Qt.CursorShape.SizeAllCursor
        self.setCursor(shape)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.windowHandle()
        if handle is None:
            return
        self._set_geometry_dragging(True)
        edges = self._edges(event.position().toPoint())
        # Native move/resize: smooth, snaps like a normal window, works across DPI.
        if edges:
            self._native_resize_edges = edges
            if not handle.startSystemResize(edges):
                self._native_resize_edges = Qt.Edge(0)
        else:
            self._native_resize_edges = Qt.Edge(0)
            handle.startSystemMove()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._native_resize_edges = Qt.Edge(0)
        self._set_geometry_dragging(False)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self.on_expand is not None:
            self.on_expand()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt API
        menu = QMenu(self)
        menu.addAction(
            f"最近提醒（{self.history_count}）",
            lambda: self.on_history and self.on_history(),
        )
        menu.addAction("開啟設定視窗", lambda: self.on_expand and self.on_expand())
        menu.addAction(
            "暫停監看" if self.armed else "開始監看",
            lambda: self.on_toggle_armed and self.on_toggle_armed(),
        )
        menu.addSeparator()
        menu.addAction("收到系統匣", lambda: self.on_close and self.on_close())
        menu.exec(event.globalPos())
