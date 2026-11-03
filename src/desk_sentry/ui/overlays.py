"""Alert surfaces: a corner rear-view mirror popup and a click-through edge flash."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QGuiApplication,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QScreen,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from desk_sentry.engine import Snapshot
from desk_sentry.sentry import Status
from desk_sentry.ui import motion, theme
from desk_sentry.ui.controls import IconButton
from desk_sentry.ui.mirror_view import MirrorView

POPUP_LINGER_MS = 2500
_SHADOW_MARGIN = theme.D["popup_shadow"]
_OVERLAY_FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
    | Qt.WindowType.WindowDoesNotAcceptFocus
)


class MirrorPopup(QWidget):
    """Small always-on-top mirror that appears only while someone is behind you."""

    def __init__(self) -> None:
        super().__init__(None, _OVERLAY_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Desk Sentry 後照鏡")
        self.corner = "top-right"
        self.mirror_width = theme.D["popup_width"]

        self._dot = QLabel()
        self._dot.setFixedWidth(theme.D["dot"])
        self._title = QLabel()
        self._time_label = QLabel("剛剛")
        self._time_label.setStyleSheet(
            f"color: {theme.TEXT_3}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        self._close_button = IconButton("close", "關閉提醒", size=theme.D["space_24"])
        self._close_button.clicked.connect(self._dismiss_now)
        self._close_button.hide()
        self._view = MirrorView(radius=theme.D["mini_view_radius"], compact=True, pickable=False)
        self._view.setMinimumSize(
            theme.D["popup_mirror_min_width"], theme.D["popup_mirror_min_height"]
        )

        header_widget = QWidget()
        header_widget.setFixedHeight(theme.D["popup_title_height"])
        top = QHBoxLayout(header_widget)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.SPACE_8)
        top.addWidget(self._dot)
        top.addWidget(self._title)
        top.addStretch(1)
        top.addWidget(self._time_label)
        top.addWidget(self._close_button)
        layout = QVBoxLayout(self)
        inset = _SHADOW_MARGIN + theme.D["popup_card_padding"]
        layout.setContentsMargins(inset, inset, inset, inset)
        layout.setSpacing(theme.D["popup_content_gap"])
        layout.addWidget(header_widget)
        layout.addWidget(self._view, 1)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_exit)
        self._dismissed = False
        self.on_dismissed: Callable[[], None] | None = None
        self._enter_animation: QParallelAnimationGroup | None = None
        self._exit_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._exit_animation.setDuration(theme.MOTION["fade_out"])
        self._exit_animation.setEasingCurve(QEasingCurve.Type.InCubic)
        self._exit_animation.finished.connect(self.hide)
        self._final_position = QPoint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(
            _SHADOW_MARGIN,
            _SHADOW_MARGIN,
            -_SHADOW_MARGIN,
            -_SHADOW_MARGIN,
        )
        p.setPen(Qt.PenStyle.NoPen)
        for spread, opacity in (
            (theme.SPACE_12, 0.07),
            (theme.SPACE_8, 0.11),
            (theme.SPACE_4, 0.17),
        ):
            shadow = card.adjusted(-spread, -spread, spread, spread)
            shadow.translate(
                0,
                spread * theme.D["popup_shadow_y"] / theme.D["popup_shadow"],
            )
            shadow_color = QColor(theme.POPUP_SHADOW)
            shadow_color.setAlpha(round(shadow_color.alpha() * opacity))
            p.setBrush(shadow_color)
            radius = theme.WINDOW_RADIUS + spread
            p.drawRoundedRect(shadow, radius, radius)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(
            card.adjusted(0.5, 0.5, -0.5, -0.5),
            theme.WINDOW_RADIUS,
            theme.WINDOW_RADIUS,
        )
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._dismiss_now()
        event.accept()

    def _dismiss_now(self) -> None:
        self._dismissed = True
        self._hide_timer.stop()
        self._stop_animations()
        self.hide()
        if self.on_dismissed is not None:
            self.on_dismissed()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt API
        self._time_label.hide()
        self._close_button.show()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self._close_button.hide()
        self._time_label.show()
        super().leaveEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt API
        self._hide_timer.stop()
        self._stop_animations()
        super().hideEvent(event)

    def update_snapshot(self, snap: Snapshot) -> None:
        alerting = snap.status in (Status.ALERT, Status.APPROACHING)
        if snap.alert_started:
            self._dismissed = False
        if alerting:
            color = theme.STATUS_COLOR[snap.status]
            title = theme.STATUS_TEXT[snap.status][0]
            self._dot.setPixmap(theme.dot_pixmap(color))
            self._title.setText(title)
            self._title.setStyleSheet(
                f"color: {color}; font-weight: 600; font-size: {theme.FONT_SIZES['pill']}px;"
            )
            self._hide_timer.stop()
            if not self._dismissed and not self.isVisible():
                self._place()
                self._show_animated()
            elif (
                not self._dismissed
                and self._exit_animation.state() is QAbstractAnimation.State.Running
            ):
                self._exit_animation.stop()
                self.setWindowOpacity(1.0)
                self.move(self._final_position)
        elif self.isVisible() and not self._hide_timer.isActive():
            self._hide_timer.start(POPUP_LINGER_MS)
        if self.isVisible():
            self._view.set_snapshot(snap)

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        card_width = self.mirror_width
        width = card_width + 2 * _SHADOW_MARGIN
        padding = theme.D["popup_card_padding"]
        mirror_content_width = card_width - 2 * padding
        card_height = (
            round(mirror_content_width * 9 / 16)
            + theme.D["popup_title_height"]
            + theme.D["popup_content_gap"]
            + 2 * padding
        )
        height = card_height + 2 * _SHADOW_MARGIN
        x = area.x() + area.width() - width if "right" in self.corner else area.x()
        y = area.y() if "top" in self.corner else area.y() + area.height() - height
        self.setGeometry(x, y, width, height)
        self._final_position = QPoint(x, y)

    def _show_animated(self) -> None:
        self._stop_animations()
        self.show()
        if not motion.animations_enabled():
            self.setWindowOpacity(1.0)
            self.move(self._final_position)
            return
        dx = theme.SPACE_12 if "right" in self.corner else -theme.SPACE_12
        dy = -theme.SPACE_12 if "top" in self.corner else theme.SPACE_12
        self.move(self._final_position + QPoint(dx, dy))
        self.setWindowOpacity(0.0)
        group = QParallelAnimationGroup(self)
        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setDuration(theme.MOTION["enter"])
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        position = QPropertyAnimation(self, b"pos", group)
        position.setStartValue(self.pos())
        position.setEndValue(self._final_position)
        position.setDuration(theme.MOTION["enter"])
        position.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity)
        group.addAnimation(position)
        self._enter_animation = group
        group.start()

    def _start_exit(self) -> None:
        if not self.isVisible():
            return
        if not motion.animations_enabled():
            self.hide()
            return
        self._exit_animation.stop()
        self._exit_animation.setStartValue(self.windowOpacity())
        self._exit_animation.setEndValue(0.0)
        self._exit_animation.start()

    def _stop_animations(self) -> None:
        self._exit_animation.stop()
        if self._enter_animation is not None:
            self._enter_animation.stop()
            self._enter_animation.deleteLater()
            self._enter_animation = None


class EdgeFlash(QWidget):
    """Pulsing glow around one screen's edge. Never takes focus or mouse input."""

    DURATION = 2.4

    def __init__(self, screen: QScreen) -> None:
        super().__init__(None, _OVERLAY_FLAGS | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.screen_ref = screen
        self._color = QColor(theme.STATUS_COLOR[Status.ALERT])
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def flash(self, color: str) -> None:
        self._color = QColor(color)
        self._started = time.monotonic()
        self.setGeometry(self.screen_ref.geometry())
        self.show()
        self._timer.start()

    def _tick(self) -> None:
        if time.monotonic() - self._started >= self.DURATION:
            self._timer.stop()
            self.hide()
        else:
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        elapsed = time.monotonic() - self._started
        # Three soft pulses that fade out.
        phase = (elapsed / self.DURATION) * 3.0
        pulse = 0.5 - 0.5 * math.cos(phase * 2 * math.pi)
        fade = max(0.0, 1.0 - elapsed / self.DURATION)
        strength = 0.35 + 0.65 * pulse * fade
        p = QPainter(self)
        rect = QRectF(self.rect())
        for i, width in enumerate((18, 10, 4)):
            color = QColor(self._color)
            color.setAlphaF(min(1.0, strength * (0.25 + 0.3 * i)))
            p.setPen(QPen(color, width))
            inset = width / 2
            p.drawRect(rect.adjusted(inset, inset, -inset, -inset))
        p.end()


class EdgeFlashGroup:
    def __init__(self) -> None:
        self._flashes: list[EdgeFlash] = []

    def hide(self) -> None:
        for flash in self._flashes:
            flash._timer.stop()
            flash.hide()

    def flash(self, color: str) -> None:
        screens = QGuiApplication.screens()
        if [f.screen_ref for f in self._flashes] != screens:
            for old in self._flashes:
                old.deleteLater()
            self._flashes = [EdgeFlash(screen) for screen in screens]
        for flash in self._flashes:
            flash.flash(color)
