"""「最近提醒」: the last few alert thumbnails, kept in memory only.

Thumbnails are never written to disk or logged; ``AlertHistory`` drops them when
the app exits (or on 清除). Thirty 240x160 RGB crops are at most ~3.5 MB. A face
feature (128 floats) travels with each item so the user can label who it was.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QHelpEvent,
    QHideEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from desk_sentry.people import Vector
from desk_sentry.stats import AlertStats, StatsSummary
from desk_sentry.ui import theme

MAX_ITEMS = 30
DISCREET_REVEAL_MS = 10_000
_SCREEN_MARGIN = theme.SPACE_12
_COLUMNS = theme.D["history_columns"]
_CHART_HEIGHT = 42
_CHART_BAR_MAX = 34
_CHART_GAP = 3


class _HourlyChart(QWidget):
    """Compact 24-hour alert histogram with per-hour tooltips."""

    def __init__(self) -> None:
        super().__init__()
        self._hours: tuple[int, ...] = (0,) * 24
        self._current_hour = 0
        self.setFixedHeight(_CHART_HEIGHT)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_summary(self, summary: StatsSummary) -> None:
        self._hours = summary.hours
        self._current_hour = summary.current_hour
        self.update()

    def bar_height(self, hour: int) -> int:
        maximum = max(self._hours, default=0)
        if maximum == 0 or self._hours[hour] == 0:
            return 1
        return max(1, round(_CHART_BAR_MAX * self._hours[hour] / maximum))

    def hour_color(self, hour: int) -> str:
        return theme.ACCENT if hour == self._current_hour else theme.TEXT_2

    def hour_tooltip(self, hour: int) -> str:
        return f"{hour} 時：{self._hours[hour]} 次"

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        bar_width = max(2.0, (width - _CHART_GAP * 23) / 24)
        baseline = self.height() - 2
        for hour in range(24):
            height = self.bar_height(hour)
            left = hour * (bar_width + _CHART_GAP)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.hour_color(hour)))
            painter.drawRoundedRect(
                QRectF(left, baseline - height, bar_width, height),
                min(2.0, bar_width / 2),
                min(2.0, height / 2),
            )
        painter.end()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            hour = self._hour_at(event.pos().x())
            QToolTip.showText(event.globalPos(), self.hour_tooltip(hour), self)
            return True
        return super().event(event)

    def _hour_at(self, x: float) -> int:
        return min(23, max(0, int(x * 24 / max(1, self.width()))))


@dataclass(slots=True)
class HistoryItem:
    shot_id: int
    when: float
    thumbnail: NDArray[np.uint8]
    face: Vector | None = None
    name: str | None = None


class AlertHistory:
    """Bounded in-memory list of alert thumbnails, newest last."""

    def __init__(self, maxlen: int = MAX_ITEMS, clock: Callable[[], float] = time.time) -> None:
        self._items: deque[HistoryItem] = deque(maxlen=maxlen)
        self._clock = clock
        self.unseen = 0
        self.on_change: Callable[[], None] | None = None

    def add(
        self,
        thumbnail: NDArray[np.uint8],
        *,
        shot_id: int = 0,
        face: Vector | None = None,
        name: str | None = None,
    ) -> None:
        """Add a thumbnail, or upgrade the item with the same ``shot_id``
        (a later frame of the same person that finally shows their face)."""

        if shot_id:
            for item in self._items:
                if item.shot_id == shot_id:
                    item.thumbnail, item.face = thumbnail, face if face is not None else item.face
                    item.name = name or item.name
                    self._changed()
                    return
        self._items.append(HistoryItem(shot_id, self._clock(), thumbnail, face, name))
        self.unseen = min(self.unseen + 1, self._items.maxlen or MAX_ITEMS)
        self._changed()

    def items(self) -> list[HistoryItem]:
        return list(reversed(self._items))  # newest first

    def mark_seen(self) -> None:
        if self.unseen:
            self.unseen = 0
            self._changed()

    def clear(self) -> None:
        self._items.clear()
        self.unseen = 0
        self._changed()

    def __len__(self) -> int:
        return len(self._items)

    @property
    def nbytes(self) -> int:
        return sum(item.thumbnail.nbytes for item in self._items)

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


def thumbnail_image(thumb: NDArray[np.uint8]) -> QImage:
    h, w = thumb.shape[:2]
    return QImage(thumb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class _Thumb(QLabel):
    def __init__(self, on_click: Callable[[], None] | None) -> None:
        super().__init__()
        self._on_click = on_click
        self._unseen = False
        self._cover_text = ""
        if on_click is not None:
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def set_unseen(self, unseen: bool) -> None:
        self._unseen = unseen
        self.update()

    def mask_image(self, text: str) -> None:
        self._cover_text = text
        self.clear()
        self.update()

    def reveal(self, image: QImage) -> None:
        self.setPixmap(QPixmap.fromImage(image))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        pixmap = self.pixmap()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        target = QRectF(self.rect())
        clip = QPainterPath()
        thumb_radius = theme.D["history_thumb_radius"]
        clip.addRoundedRect(target, thumb_radius, thumb_radius)
        p.setClipPath(clip)
        p.fillRect(target, QColor(theme.STAGE))
        if pixmap is not None and not pixmap.isNull():
            scale = max(target.width() / pixmap.width(), target.height() / pixmap.height())
            source_width = target.width() / scale
            source_height = target.height() / scale
            source = QRectF(
                (pixmap.width() - source_width) / 2,
                (pixmap.height() - source_height) / 2,
                source_width,
                source_height,
            )
            p.drawPixmap(target, pixmap, source)
        elif self._cover_text:
            p.setPen(QColor(theme.TEXT_2))
            p.drawText(target, Qt.AlignmentFlag.AlignCenter, self._cover_text)
        p.setClipping(False)
        if self._unseen:
            dot = theme.D["history_unseen_dot"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(QRectF(dot, dot, dot, dot))
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._on_click is not None:
            event.accept()
            self._on_click()
        else:
            super().mousePressEvent(event)


class HistoryWindow(QWidget):
    """Card listing the thumbnails; closes on Esc, a click outside or losing focus."""

    def __init__(
        self,
        history: AlertHistory,
        stats: AlertStats | None = None,
        *,
        discreet: bool = False,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(int(theme.D["history_width"]))
        self._history = history
        self._stats = stats
        self._discreet = discreet
        self._display_unseen = 0
        self._revealed_until: dict[int, float] = {}
        self._thumbs: dict[int, _Thumb] = {}
        self._cover_texts: dict[int, str] = {}
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._expire_reveals)
        # Set by the app: label who is on a thumbnail (only items with a face).
        self.on_label: Callable[[HistoryItem], None] | None = None
        self._labelling = False
        self._available_area: QRect | None = None

        title = QLabel("最近提醒")
        title.setStyleSheet(f"font-size: {theme.FONT_SIZES['title']}px; font-weight: 600;")
        self._count_chip = QLabel("0")
        self._count_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_chip.setStyleSheet(
            f"background: {theme.RAISED_HOVER}; border-radius: {theme.RADIUS['chip']}px;"
            f"font-size: {theme.FONT_SIZES['small']}px; font-weight: 600; padding: 2px 7px;"
        )
        self._note = QLabel()
        self._note.setStyleSheet(
            f"color: {theme.TEXT_3}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        self.clear_button = QPushButton("清除")
        self.clear_button.setFixedHeight(theme.D["control_height"])
        self.clear_button.clicked.connect(self._clear)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addSpacing(theme.SPACE_8)
        header.addWidget(self._count_chip)
        header.addStretch(1)
        header.addWidget(self.clear_button)

        self.summary_row = QWidget()
        summary_layout = QHBoxLayout(self.summary_row)
        summary_layout.setContentsMargins(0, theme.SPACE_4, 0, theme.SPACE_4)
        summary_layout.setSpacing(theme.SPACE_12)
        summary_copy = QWidget()
        summary_copy_layout = QVBoxLayout(summary_copy)
        summary_copy_layout.setContentsMargins(0, 0, 0, 0)
        summary_copy_layout.setSpacing(theme.SPACE_4)
        self.today_summary = QLabel("今天還沒有警示")
        self.today_summary.setStyleSheet(
            f"font-size: {theme.FONT_SIZES['status']}px; font-weight: 600;"
        )
        self.seven_day_summary = QLabel("近 7 天 0 次")
        self.seven_day_summary.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        summary_copy_layout.addWidget(self.today_summary)
        summary_copy_layout.addWidget(self.seven_day_summary)
        self.hourly_chart = _HourlyChart()
        summary_layout.addWidget(summary_copy)
        summary_layout.addStretch(1)
        summary_layout.addWidget(self.hourly_chart, 1)
        self.summary_row.setVisible(stats is not None)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.D["history_gap"])
        self._grid.setVerticalSpacing(theme.D["history_gap"])
        for column in range(_COLUMNS):
            self._grid.setColumnStretch(column, 1)
        self._grid_content = QWidget()
        self._grid_content.setLayout(self._grid)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("historyScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setAutoFillBackground(False)
        self._grid_content.setAutoFillBackground(False)
        self._scroll.setWidget(self._grid_content)

        self.empty_label = QWidget()
        empty_layout = QVBoxLayout(self.empty_label)
        empty_layout.setContentsMargins(0, theme.D["space_24"], 0, theme.D["space_24"])
        empty_layout.setSpacing(0)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = QLabel()
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_size = theme.D["history_empty_icon"]
        empty_icon.setPixmap(
            theme.glyph_icon("mirror", theme.TEXT_3, icon_size).pixmap(icon_size, icon_size)
        )
        empty_title = QLabel("還沒有提醒")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet(f"font-size: {theme.FONT_SIZES['pill']}px; font-weight: 500;")
        empty_detail = QLabel("有人從身後經過時，這裡會留下縮圖")
        empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_detail.setStyleSheet(
            f"color: {theme.TEXT_3}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        empty_layout.addWidget(empty_icon)
        empty_layout.addSpacing(theme.SPACE_12)
        empty_layout.addWidget(empty_title)
        empty_layout.addSpacing(theme.SPACE_4)
        empty_layout.addWidget(empty_detail)

        layout = QVBoxLayout(self)
        padding = theme.D["history_padding"]
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(theme.D["history_gap"])
        layout.addLayout(header)
        layout.addWidget(self.summary_row)
        layout.addWidget(self._note)
        layout.addWidget(self.empty_label)
        layout.addWidget(self._scroll)
        self._apply_discreet_copy()

    def set_discreet(self, discreet: bool) -> None:
        if discreet == self._discreet:
            self._apply_discreet_copy()
            return
        self._mask_all()
        self._discreet = discreet
        self._apply_discreet_copy()
        self.refresh()

    def refresh(self) -> None:
        scroll_position = self._scroll.verticalScrollBar().value()
        if self._stats is not None:
            summary = self._stats.summary()
            self.today_summary.setText(
                f"今天 {summary.today} 次" if summary.has_data else "今天還沒有警示"
            )
            self.seven_day_summary.setText(f"近 7 天 {summary.last_seven} 次")
            self.hourly_chart.set_summary(summary)
        now = time.monotonic()
        active_items = {id(item) for item in self._history.items()}
        self._revealed_until = {
            key: deadline
            for key, deadline in self._revealed_until.items()
            if key in active_items and deadline > now
        }
        for key, thumb in self._thumbs.items():
            thumb.mask_image(self._cover_texts.get(key, ""))
        self._thumbs.clear()
        self._cover_texts.clear()
        while self._grid.count():
            entry = self._grid.takeAt(0)
            widget = entry.widget() if entry is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        items = self._history.items()
        unseen = max(self._history.unseen, self._display_unseen)
        self._count_chip.setText(str(len(items)))
        self.empty_label.setVisible(not items)
        self._scroll.setVisible(bool(items))
        self.clear_button.setEnabled(bool(items))
        for index, item in enumerate(items):
            item_key = id(item)
            stamp, is_today = _history_stamp(item.when)
            labelable = item.face is not None and self.on_label is not None
            if self._discreet:
                picture = _Thumb(self._discreet_click_callback(item))
                picture.setToolTip(stamp)
                if item_key in self._revealed_until:
                    picture.reveal(thumbnail_image(item.thumbnail))
                else:
                    picture.mask_image(stamp)
            else:
                picture = _Thumb(self._label_callback(item) if labelable else None)
                picture.reveal(thumbnail_image(item.thumbnail))
                picture.setToolTip(
                    "點一下標記這是誰" if labelable else "沒拍到清楚的正臉，無法用來辨識"
                )
            self._thumbs[item_key] = picture
            self._cover_texts[item_key] = stamp
            content_width = theme.D["history_width"] - 2 * theme.D["history_padding"]
            thumb_width = (content_width - (_COLUMNS - 1) * theme.D["history_gap"]) // _COLUMNS
            thumb_height = round(thumb_width * 10 / 16)
            picture.setFixedSize(thumb_width, thumb_height)
            picture.set_unseen(index < unseen)
            age = max(0, int(time.time() - item.when))
            relative = (
                "剛剛"
                if age < 60
                else f"{age // 60} 分鐘前"
                if age < 3600
                else f"{age // 3600} 小時前"
            )
            caption = QLabel(relative if is_today else stamp)
            caption.setToolTip(stamp)
            caption.setStyleSheet(
                f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['small']}px;"
            )
            caption_row = QHBoxLayout()
            caption_row.setContentsMargins(0, 0, 0, 0)
            caption_row.setSpacing(theme.SPACE_4)
            caption_row.addWidget(caption)
            caption_row.addStretch(1)
            if item.name:
                name = QLabel(item.name)
                name.setStyleSheet(
                    f"background: {theme.RAISED_HOVER}; border-radius: {theme.RADIUS['chip']}px;"
                    f"font-size: {theme.FONT_SIZES['small']}px; font-weight: 600;"
                    f"padding: {theme.D['switch_inset']}px {theme.D['space_6']}px;"
                )
                caption_row.addWidget(name)
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(theme.SPACE_4)
            box_layout.addWidget(picture)
            box_layout.addLayout(caption_row)
            self._grid.addWidget(box, index // _COLUMNS, index % _COLUMNS)
            box.show()
        self._schedule_reveal_timer()
        self._fit_to_screen()
        self._scroll.verticalScrollBar().setValue(scroll_position)

    def _fit_to_screen(self) -> None:
        area = self._available_area
        if area is None:
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen is not None else None
        layout = self.layout()
        assert layout is not None
        self._scroll.setFixedHeight(0)
        self._grid.activate()
        layout.activate()
        chrome_height = layout.sizeHint().height()
        capacity = (
            max(0, area.height() - 2 * _SCREEN_MARGIN - chrome_height)
            if area is not None
            else self._grid.sizeHint().height()
        )
        content_width = theme.D["history_width"] - 2 * theme.D["history_padding"]
        if self._grid.sizeHint().height() > capacity:
            content_width -= self._scroll.verticalScrollBar().sizeHint().width()
        thumb_width = (content_width - (_COLUMNS - 1) * theme.D["history_gap"]) // _COLUMNS
        for picture in self._thumbs.values():
            picture.setFixedSize(thumb_width, round(thumb_width * 10 / 16))
        content_height = self._grid.sizeHint().height()
        self._grid_content.setMinimumHeight(content_height)
        self._scroll.setFixedHeight(min(content_height, capacity))
        self.adjustSize()
        if area is not None and self.isVisible():
            self._clamp_to_area(area, self.pos())

    def _clamp_to_area(self, area: QRect, position: QPoint) -> None:
        left = area.left() + _SCREEN_MARGIN
        top = area.top() + _SCREEN_MARGIN
        right = max(left, area.right() - _SCREEN_MARGIN - self.width() + 1)
        bottom = max(top, area.bottom() - _SCREEN_MARGIN - self.height() + 1)
        self.move(min(max(position.x(), left), right), min(max(position.y(), top), bottom))

    def _apply_discreet_copy(self) -> None:
        self.setWindowTitle("紀錄" if self._discreet else "Desk Sentry 最近提醒")
        self._note.setText(
            "縮圖只存在記憶體，關閉程式就清除 · 點一下暫時顯示"
            if self._discreet
            else "縮圖只存在記憶體，關閉程式就清除 · 點縮圖可標記是誰"
        )

    def _discreet_click_callback(self, item: HistoryItem) -> Callable[[], None]:
        return lambda: self._discreet_click(item)

    def _discreet_click(self, item: HistoryItem) -> None:
        key = id(item)
        now = time.monotonic()
        if self._revealed_until.get(key, 0.0) > now:
            if item.face is not None and self.on_label is not None:
                self._label(item)
            return
        self._revealed_until[key] = now + DISCREET_REVEAL_MS / 1000
        picture = self._thumbs.get(key)
        if picture is not None:
            picture.reveal(thumbnail_image(item.thumbnail))
        self._schedule_reveal_timer()

    def _expire_reveals(self) -> None:
        now = time.monotonic()
        expired = [key for key, deadline in self._revealed_until.items() if deadline <= now]
        for key in expired:
            self._revealed_until.pop(key, None)
            picture = self._thumbs.get(key)
            if picture is not None:
                picture.mask_image(self._cover_texts.get(key, ""))
        self._schedule_reveal_timer()

    def _schedule_reveal_timer(self) -> None:
        self._reveal_timer.stop()
        if not self._discreet or not self._revealed_until:
            return
        remaining = min(self._revealed_until.values()) - time.monotonic()
        self._reveal_timer.start(max(1, round(remaining * 1000)))

    def _mask_all(self) -> None:
        self._reveal_timer.stop()
        self._revealed_until.clear()
        for key, picture in self._thumbs.items():
            picture.mask_image(self._cover_texts.get(key, ""))

    def _label_callback(self, item: HistoryItem) -> Callable[[], None]:
        return lambda: self._label(item)

    def _label(self, item: HistoryItem) -> None:
        if self.on_label is None:
            return
        self._labelling = True  # the dialog takes focus; do not auto-close under it
        try:
            self.on_label(item)
        finally:
            self._labelling = False
        self.refresh()

    def open_near(self, anchor: QWidget | None = None) -> None:
        screen = (anchor.screen() if anchor is not None else None) or (
            QGuiApplication.primaryScreen()
        )
        self._available_area = screen.availableGeometry() if screen is not None else None
        self._display_unseen = self._history.unseen
        self._history.mark_seen()
        self.refresh()
        if self._available_area is not None:
            area = self._available_area
            self._clamp_to_area(
                area,
                QPoint(
                    area.center().x() - self.width() // 2, area.center().y() - self.height() // 2
                ),
            )
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _clear(self) -> None:
        self._mask_all()
        self._display_unseen = 0
        self._history.clear()
        self.refresh()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.SURFACE))
        radius = theme.WINDOW_RADIUS
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt API
        if self._discreet:
            self._mask_all()
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self.hide()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        # Close when the user clicks anywhere else (but not under our own dialog).
        if (
            event.type() == QEvent.Type.ActivationChange
            and not self.isActiveWindow()
            and not self._labelling
        ):
            self.hide()
        super().changeEvent(event)


def _history_stamp(when: float) -> tuple[str, bool]:
    item_time = time.localtime(when)
    now = time.localtime(time.time())
    same_day = (item_time.tm_year, item_time.tm_yday) == (now.tm_year, now.tm_yday)
    stamp = time.strftime("%H:%M:%S" if same_day else "%m-%d %H:%M:%S", item_time)
    return stamp, same_day
