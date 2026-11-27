"""「離座紀錄」window: who came to the desk while the user was away.

A plain titled window (close button, Esc) rather than a popup, so that people
who rarely open it still recognise how to close it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desk_sentry.ui import theme
from desk_sentry.ui.history import thumbnail_image
from desk_sentry.visits import RETENTION_DAYS, Visit, VisitLog

_COLUMNS = 3
_PHOTO_WIDTH = 200
_PHOTO_HEIGHT = 150


def visit_caption(visit: Visit, *, now: float | None = None) -> str:
    """「今天 14:05 · 坐到你的座位」 in the user's local time."""

    when = time.localtime(visit.taken_at)
    today = time.localtime(time.time() if now is None else now)
    same_day = (when.tm_year, when.tm_yday) == (today.tm_year, today.tm_yday)
    stamp = time.strftime("今天 %H:%M" if same_day else "%m/%d %H:%M", when)
    what = "坐到你的座位" if visit.seated else "靠近你的電腦"
    return f"{stamp} · {what}"


def visits_summary(count: int) -> str:
    return f"你不在座位時，有 {count} 次有人靠近" if count else "你不在座位時沒有人靠近"


class _VisitCard(QFrame):
    def __init__(self, visit: Visit) -> None:
        super().__init__()
        self.setObjectName("visitCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_4)
        self.photo = QLabel()
        self.photo.setFixedSize(_PHOTO_WIDTH, _PHOTO_HEIGHT)
        self.photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo.setStyleSheet(
            f"background: {theme.STAGE}; border-radius: {theme.RADIUS['control']}px;"
        )
        pixmap = QPixmap.fromImage(thumbnail_image(visit.image))
        self.photo.setPixmap(
            pixmap.scaled(
                _PHOTO_WIDTH,
                _PHOTO_HEIGHT,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.caption = QLabel(visit_caption(visit))
        self.caption.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['small']}px;"
        )
        layout.addWidget(self.photo)
        layout.addWidget(self.caption)


class VisitWindow(QWidget):
    def __init__(self, log: VisitLog) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("離座紀錄 - Desk Sentry")
        self.setWindowIcon(theme.app_icon())
        self.setStyleSheet(f"background: {theme.BG}; color: {theme.TEXT};")
        self._log = log
        self.on_cleared: Callable[[], None] | None = None

        self.summary = QLabel()
        self.summary.setStyleSheet(f"font-size: {theme.FONT_SIZES['title']}px; font-weight: 600;")
        where = "只保存在這台電腦並加密" if log.persistent else "只在程式開著時保留"
        self.note = QLabel(f"照片{where}，{RETENTION_DAYS} 天後自動刪除。最後一張常是你自己回座。")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.TEXT_3}; font-size: {theme.FONT_SIZES['small']}px;")
        self.clear_button = QPushButton("全部刪除")
        self.clear_button.setObjectName("secondaryButton")
        self.clear_button.setFixedHeight(theme.D["control_height"])
        self.clear_button.clicked.connect(self._confirm_clear)
        self.close_button = QPushButton("關閉")
        self.close_button.setFixedHeight(theme.D["control_height"])
        self.close_button.clicked.connect(self.close)

        header = QHBoxLayout()
        header.addWidget(self.summary, 1)
        header.addWidget(self.clear_button)
        header.addWidget(self.close_button)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(theme.SPACE_12)
        self._grid.setVerticalSpacing(theme.SPACE_12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid_host = QWidget()
        grid_host.setLayout(self._grid)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(grid_host)

        self.empty = QLabel(
            "目前沒有紀錄。\n開啟「離座看守」後，你離開座位時有人靠近就會拍下一張照片。"
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet(f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['body']}px;")

        layout = QVBoxLayout(self)
        padding = theme.D["space_20"]
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(theme.SPACE_12)
        layout.addLayout(header)
        layout.addWidget(self.note)
        layout.addWidget(self.empty, 1)
        layout.addWidget(self._scroll, 1)
        self.cards: list[_VisitCard] = []
        width = _COLUMNS * _PHOTO_WIDTH + (_COLUMNS - 1) * theme.SPACE_12 + 2 * padding + 24
        self.resize(width, 560)
        self.refresh()

    def refresh(self) -> None:
        for card in self.cards:
            self._grid.removeWidget(card)
            card.deleteLater()
        self.cards = []
        items = self._log.items()
        for index, visit in enumerate(items):
            card = _VisitCard(visit)
            self._grid.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self.cards.append(card)
        self.summary.setText(visits_summary(len(items)))
        self.empty.setVisible(not items)
        self._scroll.setVisible(bool(items))
        self.clear_button.setEnabled(bool(items))

    def _confirm_clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "刪除離座紀錄",
            "要刪除全部照片嗎？刪除後無法復原。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.clear()

    def clear(self) -> None:
        self._log.clear()
        self.refresh()
        if self.on_cleared is not None:
            self.on_cleared()

    def open(self) -> None:
        self.refresh()
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
