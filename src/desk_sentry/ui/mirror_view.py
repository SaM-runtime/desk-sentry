"""Live, horizontally mirrored camera view with role-coloured person boxes."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QHideEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.ui import motion, theme

_ALERT_ROLES = (Role.INTRUDER, Role.APPROACHING)
_NO_IMAGE_STATUSES = (Status.PAUSED, Status.NO_CAMERA, Status.ERROR)


def _lerp_box(
    start: tuple[float, float, float, float],
    end: tuple[float, float, float, float],
    amount: float,
) -> tuple[float, float, float, float]:
    values = tuple(a + (b - a) * amount for a, b in zip(start, end, strict=True))
    return values  # type: ignore[return-value]


@dataclass(slots=True)
class _TrackPaintState:
    track: TrackView
    box: tuple[float, float, float, float]
    target: tuple[float, float, float, float]
    opacity: float
    position: QVariantAnimation
    fade: QVariantAnimation
    last_tick: float
    present: bool = True


class MirrorView(QWidget):
    """Shared mirror stage.

    ``animations_running`` is a public introspection hook for tests and runtime
    diagnostics. It is true while any status, track, fade, or breathing
    ``QVariantAnimation`` owned by this view is running.
    """

    picked = Signal(float, float)
    resume_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: float = theme.RADIUS_STAGE,
        compact: bool = False,
        pickable: bool = True,
        badge: bool = False,
        align_top: bool = False,
        border_color: QColor | str = theme.BORDER,
    ) -> None:
        super().__init__(parent)
        self._snapshot = Snapshot(Status.PAUSED)
        self._image: QImage | None = None
        self._radius = radius
        self._compact = compact
        self._pickable = pickable
        self._badge = badge
        self._unseen = 0
        self._align_top = align_top
        self._border_color = QColor(border_color)
        self._normal_border_color = QColor(border_color)
        self._border_target = QColor(border_color)
        self._stage_hovered = False
        self._resume_rect = QRectF()
        self._status_pill_rect: QRectF | None = None
        self._painted_chip_rects: list[QRectF] = []
        self._tracks: dict[int, _TrackPaintState] = {}

        self._status_color = QColor(theme.STATUS_COLOR[Status.PAUSED])
        self._status_target = QColor(self._status_color)
        self._status_animation = QVariantAnimation(self)
        self._status_animation.setDuration(theme.MOTION["color"])
        self._status_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._status_animation.valueChanged.connect(self._advance_status_color)
        self._border_animation = QVariantAnimation(self)
        self._border_animation.setDuration(theme.MOTION["color"])
        self._border_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._border_animation.valueChanged.connect(self._advance_border_color)

        self._breath = 0.0
        self._breath_animation = QVariantAnimation(self)
        self._breath_animation.setStartValue(0.0)
        self._breath_animation.setEndValue(1.0)
        self._breath_animation.setLoopCount(-1)
        self._breath_animation.valueChanged.connect(self._advance_breath)

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(*theme.MIRROR_MIN_SIZE)

    @property
    def animations_running(self) -> bool:
        """Whether this view currently owns a running ``QVariantAnimation``."""

        animations = [self._status_animation, self._border_animation, self._breath_animation]
        for state in self._tracks.values():
            animations.extend((state.position, state.fade))
        return any(
            animation.state() is QAbstractAnimation.State.Running for animation in animations
        )

    @property
    def status_pill_rect(self) -> QRectF | None:
        """Last painted status-pill geometry, or ``None`` when no pill is shown."""

        return QRectF(self._status_pill_rect) if self._status_pill_rect is not None else None

    @property
    def chip_rects(self) -> tuple[QRectF, ...]:
        """Last painted chip rectangles for geometry-focused UI tests."""

        return tuple(QRectF(rect) for rect in self._painted_chip_rects)

    def set_unseen(self, count: int) -> None:
        if count != self._unseen:
            self._unseen = count
            self.update()

    def set_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        frame = snapshot.frame
        if frame is None:
            # Status-only snapshots arrive while the preview was hidden; keep the
            # last picture unless the camera really is gone.
            if snapshot.status in _NO_IMAGE_STATUSES:
                self._image = None
        else:
            h, w = frame.shape[:2]
            # Mirror like a real rear-view mirror: someone behind your left
            # shoulder shows up on the left.
            self._image = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_RGB888).flipped(
                Qt.Orientation.Horizontal
            )
        self._sync_status_color(snapshot.status)
        self._sync_border_color(snapshot.status)
        self._sync_tracks(snapshot.tracks)
        self._sync_breathing()
        self.update()

    def _image_rect(self) -> QRectF | None:
        if self._image is None:
            return None
        inset = theme.STAGE_BORDER_INSET
        outer = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        iw, ih = self._image.width(), self._image.height()
        scale = min(outer.width() / iw, outer.height() / ih)
        dw, dh = iw * scale, ih * scale
        top = outer.top() if self._align_top else outer.center().y() - dh / 2
        return QRectF(outer.center().x() - dw / 2, top, dw, dh)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            self._image is None
            and self._snapshot.status is Status.PAUSED
            and self._resume_rect.contains(event.position())
            and event.button() is Qt.MouseButton.LeftButton
        ):
            self.resume_requested.emit()
            event.accept()
            return
        if not self._pickable:
            event.ignore()
            return
        target = self._image_rect()
        if target is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()
        if not target.contains(pos):
            super().mousePressEvent(event)
            return
        x = 1.0 - (pos.x() - target.left()) / target.width()
        y = (pos.y() - target.top()) / target.height()
        self.picked.emit(x, y)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt API
        self._stage_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self._stage_hovered = False
        self.update()
        super().leaveEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._sync_breathing()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt API
        self._status_animation.stop()
        self._status_color = QColor(self._status_target)
        self._border_animation.stop()
        self._border_color = QColor(self._border_target)
        self._breath_animation.stop()
        self._breath = 0.0
        for track_id, state in tuple(self._tracks.items()):
            state.position.stop()
            state.fade.stop()
            state.box = state.target
            if state.present:
                state.opacity = 1.0
            else:
                self._discard_track(track_id)
        super().hideEvent(event)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return round(width * theme.MIRROR_ASPECT_HEIGHT / theme.MIRROR_ASPECT_WIDTH)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        self._status_pill_rect = None
        self._painted_chip_rects.clear()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = theme.STAGE_BORDER_INSET
        outer = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        stage = QPainterPath()
        stage.addRoundedRect(outer, self._radius, self._radius)
        p.fillPath(stage, QColor(theme.STAGE))

        frame_rect = self._image_rect()
        if frame_rect is None:
            self._draw_no_image(p, outer)
        else:
            p.save()
            p.setClipPath(stage)
            image = self._image
            assert image is not None
            p.drawImage(frame_rect, image)
            self._draw_overlays(p, frame_rect)
            p.restore()

        if frame_rect is not None:
            if not self._compact:
                self._draw_main_status(p, frame_rect)
            elif self._badge and self._snapshot.status is not Status.CLEAR:
                self._draw_compact_badge(p, frame_rect)
            p.save()
            p.setClipPath(stage)
            for state in self._tracks.values():
                if state.track.role is Role.SELF and self._compact:
                    continue
                p.setOpacity(state.opacity)
                self._draw_track_chip(
                    p, frame_rect, self._to_view(frame_rect, state.box), state.track
                )
            p.restore()
            if self._unseen:
                self._draw_unseen(p, frame_rect)

        self._draw_stage_border(p, stage)
        p.end()

    def _to_view(self, target: QRectF, box: tuple[float, float, float, float]) -> QRectF:
        x1, y1, x2, y2 = box
        left = target.left() + (1.0 - x2) * target.width()
        right = target.left() + (1.0 - x1) * target.width()
        return QRectF(
            left, target.top() + y1 * target.height(), right - left, (y2 - y1) * target.height()
        )

    def _clamp_chip_rect(self, rect: QRectF, target: QRectF) -> QRectF:
        """Clamp a chip wholly inside the image's tokenized safe boundary."""

        margin = theme.CHIP_BOUNDARY_MARGIN
        bounds = target.adjusted(margin, margin, -margin, -margin)
        if bounds.width() <= 0 or bounds.height() <= 0:
            return QRectF()
        width = min(max(0.0, rect.width()), bounds.width())
        height = min(max(0.0, rect.height()), bounds.height())
        left = min(max(rect.left(), bounds.left()), bounds.right() - width)
        top = min(max(rect.top(), bounds.top()), bounds.bottom() - height)
        return QRectF(left, top, width, height)

    def _record_chip(self, rect: QRectF, target: QRectF, owner: QRectF | None = None) -> QRectF:
        chip = self._clamp_chip_rect(rect, target)
        pill = self._status_pill_rect
        if pill is not None and chip.intersects(pill):
            chip.moveTop((owner if owner is not None else target).top() + theme.TRACK_CHIP_GAP)
            chip = self._clamp_chip_rect(chip, target)
            if chip.intersects(pill):
                chip.moveBottom(pill.top() - theme.CHIP_BOUNDARY_MARGIN)
                chip = self._clamp_chip_rect(chip, target)
        if not chip.isEmpty():
            self._painted_chip_rects.append(QRectF(chip))
        return chip

    def _sync_status_color(self, status: Status) -> None:
        target = QColor(theme.STATUS_COLOR[status])
        if target == self._status_target:
            if not motion.animations_enabled() or not self.isVisible():
                self._status_animation.stop()
                self._status_color = QColor(target)
            return
        self._status_target = target
        if not motion.animations_enabled() or not self.isVisible():
            self._status_animation.stop()
            self._status_color = QColor(target)
            return
        self._status_animation.stop()
        self._status_animation.setStartValue(QColor(self._status_color))
        self._status_animation.setEndValue(target)
        self._status_animation.setDuration(theme.MOTION["color"])
        self._status_animation.start()

    def _advance_status_color(self, value: object) -> None:
        if isinstance(value, QColor):
            self._status_color = QColor(value)
            self.update()

    def _sync_border_color(self, status: Status) -> None:
        target = QColor(
            theme.STATUS_COLOR[status]
            if status in (Status.ALERT, Status.APPROACHING)
            else self._normal_border_color
        )
        if not motion.animations_enabled() or not self.isVisible():
            self._border_animation.stop()
            self._border_target = target
            self._border_color = QColor(target)
        elif target != self._border_target:
            self._border_animation.stop()
            self._border_target = target
            self._border_animation.setStartValue(self._border_color)
            self._border_animation.setEndValue(target)
            self._border_animation.start()

    def _advance_border_color(self, value: object) -> None:
        if isinstance(value, QColor):
            self._border_color = QColor(value)
            self.update()

    def _sync_tracks(self, tracks: tuple[TrackView, ...]) -> None:
        incoming = {track.track_id: track for track in tracks}
        animated = motion.animations_enabled() and self.isVisible()
        now = time.monotonic()
        for track_id, track in incoming.items():
            state = self._tracks.get(track_id)
            if state is None:
                position = QVariantAnimation(self)
                position.setStartValue(0.0)
                position.setEndValue(1.0)
                position.setDuration(theme.TRACK_SMOOTH_DURATION)
                fade = QVariantAnimation(self)
                state = _TrackPaintState(
                    track=track,
                    box=track.box,
                    target=track.box,
                    opacity=0.0 if animated else 1.0,
                    position=position,
                    fade=fade,
                    last_tick=now,
                )
                position.valueChanged.connect(
                    lambda _value, tid=track_id: self._advance_track_position(tid)
                )
                position.finished.connect(lambda tid=track_id: self._finish_track_position(tid))
                fade.valueChanged.connect(
                    lambda value, tid=track_id: self._advance_track_fade(tid, value)
                )
                fade.finished.connect(lambda tid=track_id: self._finish_track_fade(tid))
                self._tracks[track_id] = state
                if animated:
                    self._start_track_fade(
                        state,
                        1.0,
                        theme.MOTION["fade_in"],
                        QEasingCurve.Type.OutCubic,
                    )
                continue
            was_present = state.present
            moved = (
                max(abs(a - b) for a, b in zip(state.target, track.box, strict=True))
                >= theme.TRACK_SNAP_EPSILON
            )
            if animated and moved:
                # Account for the part of the frame interval since the last Qt
                # animation tick before accepting a new target.
                self._advance_track_position(track_id)
            state.track = track
            state.target = track.box
            state.present = True
            if not animated:
                state.position.stop()
                state.box = track.box
                state.opacity = 1.0
                state.fade.stop()
            else:
                if moved:
                    state.last_tick = now
                    state.position.stop()
                    state.position.start()
                if not was_present:
                    self._start_track_fade(
                        state,
                        1.0,
                        theme.MOTION["fade_in"],
                        QEasingCurve.Type.OutCubic,
                    )

        for track_id, state in tuple(self._tracks.items()):
            if track_id in incoming:
                continue
            if not state.present:
                if not animated:
                    self._discard_track(track_id)
                continue
            state.present = False
            state.position.stop()
            if animated:
                self._start_track_fade(
                    state,
                    0.0,
                    theme.MOTION["fade_out"],
                    QEasingCurve.Type.InCubic,
                )
            else:
                self._discard_track(track_id)

    def _advance_track_position(self, track_id: int) -> None:
        state = self._tracks.get(track_id)
        if state is None or not state.present:
            return
        now = time.monotonic()
        elapsed = max(0.0, now - state.last_tick)
        state.last_tick = now
        tau_seconds = theme.MOTION["track_tau"] / 1000
        amount = 1.0 - math.exp(-elapsed / tau_seconds)
        state.box = _lerp_box(state.box, state.target, amount)
        if (
            max(abs(a - b) for a, b in zip(state.box, state.target, strict=True))
            < theme.TRACK_SNAP_EPSILON
        ):
            state.box = state.target
            state.position.stop()
        self.update()

    def _finish_track_position(self, track_id: int) -> None:
        state = self._tracks.get(track_id)
        if state is not None:
            state.box = state.target
            self.update()

    def _start_track_fade(
        self,
        state: _TrackPaintState,
        end: float,
        duration: int,
        easing: QEasingCurve.Type,
    ) -> None:
        state.fade.stop()
        state.fade.setStartValue(state.opacity)
        state.fade.setEndValue(end)
        state.fade.setDuration(duration)
        state.fade.setEasingCurve(easing)
        state.fade.start()

    def _advance_track_fade(self, track_id: int, value: object) -> None:
        state = self._tracks.get(track_id)
        if state is not None and isinstance(value, (int, float)):
            state.opacity = float(value)
            self.update()

    def _finish_track_fade(self, track_id: int) -> None:
        state = self._tracks.get(track_id)
        if state is not None and not state.present and state.opacity <= 0.001:
            self._discard_track(track_id)

    def _discard_track(self, track_id: int) -> None:
        state = self._tracks.pop(track_id, None)
        if state is not None:
            state.position.stop()
            state.fade.stop()
            # These lambdas retain the view. Release them while this call still
            # owns it, not from a deferred child destructor: releasing the last
            # view reference there would re-enter destruction of that same child.
            state.position.valueChanged.disconnect()
            state.position.finished.disconnect()
            state.fade.valueChanged.disconnect()
            state.fade.finished.disconnect()
            state.position.deleteLater()
            state.fade.deleteLater()
            self._sync_breathing()
            self.update()

    def _sync_breathing(self) -> None:
        status = self._snapshot.status
        should_run = (
            motion.animations_enabled()
            and self.isVisible()
            and status in (Status.ALERT, Status.APPROACHING)
        )
        if not should_run:
            self._breath_animation.stop()
            self._breath = 0.0
            return
        duration = theme.MOTION["alert"] if status is Status.ALERT else theme.MOTION["approaching"]
        if (
            self._breath_animation.state() is not QAbstractAnimation.State.Running
            or self._breath_animation.duration() != duration
        ):
            self._breath_animation.stop()
            self._breath_animation.setDuration(duration)
            self._breath_animation.start()

    def _advance_breath(self, value: object) -> None:
        if isinstance(value, (int, float)):
            phase = float(value)
            self._breath = 0.5 - 0.5 * math.cos(phase * math.tau)
            self.update()

    def _draw_no_image(self, p: QPainter, outer: QRectF) -> None:
        title, detail = theme.STATUS_TEXT[self._snapshot.status]
        paused = self._snapshot.status is Status.PAUSED and not self._compact
        total_height = (
            theme.NO_IMAGE_ICON
            + theme.SPACE_12
            + theme.NO_IMAGE_TITLE_HEIGHT
            + theme.SPACE_6
            + theme.NO_IMAGE_DETAIL_HEIGHT
            + (theme.SPACE_12 + theme.PRIMARY_BUTTON_HEIGHT if paused else 0)
        )
        top = outer.center().y() - total_height / 2

        icon_size = theme.NO_IMAGE_ICON
        icon = QRectF(outer.center().x() - icon_size / 2, top, icon_size, icon_size)
        theme.glyph_icon("mirror", theme.TEXT_3, icon_size).paint(p, icon.toRect())

        title_rect = QRectF(
            outer.left(),
            icon.bottom() + theme.SPACE_12,
            outer.width(),
            theme.NO_IMAGE_TITLE_HEIGHT,
        )
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_STATUS_PX)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT))
        p.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, title)

        detail_padding = theme.SPACING_16
        detail_rect = QRectF(
            outer.left() + detail_padding,
            title_rect.bottom() + theme.SPACE_6,
            outer.width() - 2 * detail_padding,
            theme.NO_IMAGE_DETAIL_HEIGHT,
        )
        font.setPixelSize(theme.FONT_BODY_PX)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT_2))
        p.drawText(detail_rect, Qt.AlignmentFlag.AlignCenter, detail)

        self._resume_rect = QRectF()
        if paused:
            button_width = max(
                theme.PRIMARY_BUTTON_MIN_WIDTH,
                p.fontMetrics().horizontalAdvance("開始監看") + 2 * theme.PRIMARY_BUTTON_H_PADDING,
            )
            self._resume_rect = QRectF(
                outer.center().x() - button_width / 2,
                detail_rect.bottom() + theme.SPACE_12,
                button_width,
                theme.PRIMARY_BUTTON_HEIGHT,
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            button_radius = theme.PRIMARY_BUTTON_HEIGHT / 2
            p.drawRoundedRect(self._resume_rect, button_radius, button_radius)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            p.setPen(QColor(theme.ON_ACCENT))
            p.drawText(self._resume_rect, Qt.AlignmentFlag.AlignCenter, "開始監看")

    def _draw_overlays(self, p: QPainter, target: QRectF) -> None:
        if self._snapshot.self_zone is not None and not self._compact and self._stage_hovered:
            color = QColor(theme.ACCENT)
            color.setAlphaF(theme.SELF_ZONE_ALPHA)
            pen = QPen(color, theme.STAGE_BORDER_WIDTH, Qt.PenStyle.DashLine)
            pen.setDashPattern(list(theme.SELF_ZONE_DASH))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            zone_radius = theme.RADIUS["control"]
            p.drawRoundedRect(
                self._to_view(target, self._snapshot.self_zone), zone_radius, zone_radius
            )

        for state in self._tracks.values():
            if state.track.role is Role.SELF and self._compact:
                continue
            p.save()
            p.setOpacity(state.opacity)
            rect = self._to_view(target, state.box)
            if state.track.role is Role.SELF:
                self._draw_brackets(p, rect, QColor(theme.ACCENT), theme.SELF_BRACKET_WIDTH)
            elif state.track.role in _ALERT_ROLES:
                self._draw_alert_box(p, rect, QColor(theme.ROLE_COLOR[state.track.role]))
            else:
                color = QColor(theme.ROLE_COLOR[state.track.role])
                color.setAlphaF(theme.ROLE_BOX_ALPHA)
                self._draw_brackets(p, rect, color, theme.ROLE_BRACKET_WIDTH)
            p.restore()

    def _draw_brackets(self, p: QPainter, rect: QRectF, color: QColor, width: float) -> None:
        arm = min(
            theme.BRACKET_MAX_ARM,
            rect.width() * theme.BRACKET_ARM_RATIO,
            rect.height() * theme.BRACKET_ARM_RATIO,
        )
        p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for corner, dx, dy in (
            (rect.topLeft(), 1, 1),
            (rect.topRight(), -1, 1),
            (rect.bottomLeft(), 1, -1),
            (rect.bottomRight(), -1, -1),
        ):
            p.drawLine(corner, corner + QPointF(dx * arm, 0))
            p.drawLine(corner, corner + QPointF(0, dy * arm))

    def _draw_alert_box(self, p: QPainter, rect: QRectF, color: QColor) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        glow_layers = (
            (theme.ALERT_GLOW_OUTER_WIDTH, theme.ALERT_GLOW_OUTER_ALPHA),
            (theme.ALERT_GLOW_INNER_WIDTH, theme.ALERT_GLOW_INNER_ALPHA),
        )
        for width, alpha in glow_layers:
            glow = QColor(color)
            glow.setAlphaF(alpha)
            p.setPen(QPen(glow, width))
            p.drawRoundedRect(rect, theme.RADIUS_CHIP, theme.RADIUS_CHIP)
        p.setPen(QPen(color, theme.ALERT_BOX_WIDTH))
        p.drawRoundedRect(rect, theme.RADIUS_CHIP, theme.RADIUS_CHIP)

    def _draw_track_chip(self, p: QPainter, target: QRectF, rect: QRectF, track: TrackView) -> None:
        role_text = theme.ROLE_TEXT[track.role]
        text = (
            f"{track.name} · {role_text}"
            if track.name and track.role in _ALERT_ROLES
            else track.name or role_text
        )
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_SMALL_PX)
        font.setWeight(QFont.Weight.DemiBold if track.role in _ALERT_ROLES else QFont.Weight.Medium)
        p.setFont(font)
        metrics = p.fontMetrics()
        alerting = track.role in _ALERT_ROLES
        dot_space = (
            0
            if alerting or track.role is Role.SELF
            else theme.D["small_dot"] + theme.TRACK_CHIP_DOT_GAP
        )
        bounds_width = max(0.0, target.width() - 2 * theme.CHIP_BOUNDARY_MARGIN)
        fixed_width = 2 * theme.TRACK_CHIP_H_PADDING + dot_space
        text_width = max(0, round(bounds_width - fixed_width))
        display_text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, text_width)
        width = metrics.horizontalAdvance(display_text) + fixed_width
        top = rect.top() - theme.CHIP_HEIGHT - theme.TRACK_CHIP_GAP
        if top < target.top() + theme.CHIP_BOUNDARY_MARGIN:
            top = rect.top() + theme.TRACK_CHIP_GAP
        chip = self._record_chip(QRectF(rect.left(), top, width, theme.CHIP_HEIGHT), target, rect)
        if chip.isEmpty():
            return
        p.setPen(Qt.PenStyle.NoPen)
        if alerting:
            p.setBrush(QColor(theme.ROLE_COLOR[track.role]))
            p.drawRoundedRect(chip, theme.RADIUS_CHIP, theme.RADIUS_CHIP)
            p.setPen(QColor(theme.ON_STATUS))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, display_text)
            return
        p.setBrush(QColor(theme.OVERLAY))
        p.drawRoundedRect(chip, theme.RADIUS_CHIP, theme.RADIUS_CHIP)
        text_color = QColor(theme.TEXT_2 if track.role is Role.PENDING else theme.TEXT)
        if track.role is Role.SELF:
            p.setPen(text_color)
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, display_text)
            return
        color = QColor(theme.ROLE_COLOR[track.role])
        p.setBrush(color)
        dot = theme.D["small_dot"]
        p.drawEllipse(
            QRectF(
                chip.left() + theme.TRACK_CHIP_H_PADDING,
                chip.center().y() - dot / 2,
                dot,
                dot,
            )
        )
        p.setPen(text_color)
        p.drawText(
            chip.adjusted(dot_space, 0, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            display_text,
        )

    def _draw_main_status(self, p: QPainter, target: QRectF) -> None:
        title, detail = theme.STATUS_TEXT[self._snapshot.status]
        safe_right = target.right() - theme.CHIP_BOUNDARY_MARGIN
        left = target.left() + theme.STATUS_PILL_INSET
        available = max(0.0, safe_right - left)
        title_font = QFont(self.font())
        title_font.setPixelSize(theme.FONT_PILL_PX)
        title_font.setWeight(QFont.Weight.DemiBold)
        body_font = QFont(self.font())
        body_font.setPixelSize(theme.FONT_BODY_PX)
        p.setFont(title_font)
        title_metrics = p.fontMetrics()
        p.setFont(body_font)
        body_metrics = p.fontMetrics()
        dot = theme.D["dot"]
        fixed = 2 * theme.STATUS_PILL_H_PADDING + dot + theme.STATUS_PILL_DOT_GAP
        preferred = (
            fixed
            + title_metrics.horizontalAdvance(title)
            + body_metrics.horizontalAdvance(f" · {detail}")
        )
        draw_detail = preferred <= available
        width: float
        if draw_detail:
            display_title = title
            width = float(preferred)
        else:
            display_title = title_metrics.elidedText(
                title, Qt.TextElideMode.ElideRight, max(0, round(available - fixed))
            )
            width = min(available, fixed + title_metrics.horizontalAdvance(display_title))
        if width <= 0:
            return
        chip = self._record_chip(
            QRectF(
                left,
                target.bottom() - theme.STATUS_PILL_INSET - theme.STATUS_PILL_HEIGHT,
                width,
                theme.STATUS_PILL_HEIGHT,
            ),
            target,
        )
        if chip.isEmpty():
            return
        self._status_pill_rect = QRectF(chip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.OVERLAY))
        pill_radius = theme.STATUS_PILL_HEIGHT / 2
        p.drawRoundedRect(chip, pill_radius, pill_radius)
        p.setBrush(self._status_color)
        p.drawEllipse(
            QRectF(
                chip.left() + theme.STATUS_PILL_H_PADDING,
                chip.center().y() - dot / 2,
                dot,
                dot,
            )
        )
        x = chip.left() + theme.STATUS_PILL_H_PADDING + dot + theme.STATUS_PILL_DOT_GAP
        p.setFont(title_font)
        p.setPen(QColor(theme.TEXT))
        title_width = title_metrics.horizontalAdvance(display_title)
        p.drawText(
            QRectF(x, chip.top(), title_width, chip.height()),
            Qt.AlignmentFlag.AlignVCenter,
            display_title,
        )
        if draw_detail:
            p.setFont(body_font)
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(
                QRectF(
                    x + title_width,
                    chip.top(),
                    chip.right() - x - title_width - theme.STATUS_PILL_H_PADDING,
                    chip.height(),
                ),
                Qt.AlignmentFlag.AlignVCenter,
                f" · {detail}",
            )

    def _draw_compact_badge(self, p: QPainter, target: QRectF) -> None:
        text = theme.STATUS_TEXT[self._snapshot.status][0]
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_SMALL_PX)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        metrics = p.fontMetrics()
        dot = theme.D["small_dot"]
        fixed_width = 2 * theme.COMPACT_PILL_H_PADDING + dot + theme.TRACK_CHIP_DOT_GAP
        available = max(
            0,
            round(target.width() - 2 * theme.CHIP_BOUNDARY_MARGIN - fixed_width),
        )
        display_text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
        width = metrics.horizontalAdvance(display_text) + fixed_width
        chip = self._record_chip(
            QRectF(
                target.left() + theme.COMPACT_PILL_INSET,
                target.bottom() - theme.COMPACT_PILL_INSET - theme.COMPACT_PILL_HEIGHT,
                width,
                theme.COMPACT_PILL_HEIGHT,
            ),
            target,
        )
        if chip.isEmpty():
            return
        self._status_pill_rect = QRectF(chip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.OVERLAY))
        pill_radius = theme.COMPACT_PILL_HEIGHT / 2
        p.drawRoundedRect(chip, pill_radius, pill_radius)
        p.setBrush(self._status_color)
        p.drawEllipse(
            QRectF(
                chip.left() + theme.COMPACT_PILL_H_PADDING,
                chip.center().y() - dot / 2,
                dot,
                dot,
            )
        )
        p.setPen(QColor(theme.TEXT))
        text_offset = dot + theme.TRACK_CHIP_DOT_GAP
        p.drawText(
            chip.adjusted(text_offset, 0, 0, 0),
            Qt.AlignmentFlag.AlignCenter,
            display_text,
        )

    def _draw_unseen(self, p: QPainter, target: QRectF) -> None:
        text = str(self._unseen)
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_SIZES["caption"])
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        metrics = p.fontMetrics()
        height = theme.UNSEEN_HEIGHT
        width = max(height, metrics.horizontalAdvance(text) + 2 * theme.TRACK_CHIP_H_PADDING)
        chip = self._record_chip(
            QRectF(
                target.right() - width - theme.UNSEEN_INSET,
                target.bottom() - height - theme.UNSEEN_INSET,
                width,
                height,
            ),
            target,
        )
        if chip.isEmpty():
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(chip, height / 2, height / 2)
        p.setPen(QColor(theme.ON_ACCENT))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_stage_border(self, p: QPainter, stage: QPainterPath) -> None:
        alerting = self._snapshot.status in (Status.ALERT, Status.APPROACHING)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if alerting:
            if motion.animations_enabled():
                glow = QColor(self._status_color)
                alpha_range = theme.STAGE_GLOW_ALPHA_MAX - theme.STAGE_GLOW_ALPHA_MIN
                glow.setAlphaF(theme.STAGE_GLOW_ALPHA_MIN + alpha_range * self._breath)
                p.save()
                p.setClipPath(stage)
                p.setPen(QPen(glow, theme.STAGE_GLOW_WIDTH))
                p.drawPath(stage)
                p.restore()
            p.setPen(QPen(self._border_color, theme.STAGE_ALERT_BORDER_WIDTH))
        else:
            p.setPen(QPen(self._border_color, theme.STAGE_BORDER_WIDTH))
        p.drawPath(stage)
