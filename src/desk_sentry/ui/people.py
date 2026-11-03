"""Labelling people and managing their alert level (「警戒程度」)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from desk_sentry.people import LEVEL_TEXT, Level, PeopleBook
from desk_sentry.ui import theme

LEVEL_HINT = {
    Level.HIGH: "閃光、提示音與「同時執行」都照設定進行",
    Level.MEDIUM: "小鏡子／彈窗變紅並顯示名字，不閃光、不發聲",
    Level.LOW: "只在畫面上標示名字，不算提醒",
}


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
    return label


class LabelDialog(QDialog):
    """「這是誰？」: name (new or existing) plus alert level."""

    def __init__(
        self,
        thumbnail: QImage | None,
        names: list[str],
        current: str | None = None,
        level_of: Callable[[str], Level | None] = lambda _name: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("這是誰？")
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(f"QDialog {{ background: {theme.SURFACE}; }}")
        self._level_of = level_of

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        if thumbnail is not None:
            picture = QLabel()
            picture.setPixmap(QPixmap.fromImage(thumbnail))
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(picture)

        layout.addWidget(QLabel("名字"))
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(names)
        self.name.setCurrentText(current or "")
        line = self.name.lineEdit()
        if line is not None:
            line.setPlaceholderText("輸入新名字，或選擇已認得的人")
            line.setMaxLength(40)
        self.name.currentTextChanged.connect(self._name_changed)
        layout.addWidget(self.name)

        layout.addWidget(QLabel("警戒程度"))
        self.levels = QButtonGroup(self)
        for level in Level:
            button = QRadioButton(LEVEL_TEXT[level])
            button.setProperty("level", level.value)
            self.levels.addButton(button)
            layout.addWidget(button)
            layout.addWidget(_muted(LEVEL_HINT[level]))
        self._select(Level.HIGH)
        layout.addWidget(
            _muted("只會記住臉部特徵（一組數字），不存照片；以你的 Windows 帳號加密。")
        )

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("記住")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self._name_changed(self.name.currentText())

    def _select(self, level: Level) -> None:
        for button in self.levels.buttons():
            button.setChecked(button.property("level") == level.value)

    def _name_changed(self, text: str) -> None:
        self.save_button.setEnabled(bool(text.strip()))
        known = self._level_of(text)
        if known is not None:
            self._select(known)

    @property
    def result_name(self) -> str:
        return self.name.currentText().strip()

    @property
    def result_level(self) -> Level:
        checked = self.levels.checkedButton()
        return Level(checked.property("level")) if checked else Level.HIGH


class PeopleEditor(QWidget):
    """Settings section listing labelled people with a level selector each."""

    def __init__(self, book: PeopleBook, on_changed: Callable[[], None]) -> None:
        super().__init__()
        self._book = book
        self._on_changed = on_changed
        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        self.empty = _muted("還沒有認得的人。在「最近提醒」點一張拍到正臉的縮圖就能加入。")
        self.clear_button = QPushButton("全部刪除")
        self.clear_button.clicked.connect(self._clear_all)
        # Owned by this widget, so it can never fire after the widget is gone.
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(3000)
        self._confirm_timer.timeout.connect(lambda: self.clear_button.setText("全部刪除"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.empty)
        layout.addLayout(self._rows)
        layout.addWidget(self.clear_button)
        storage = (
            "臉部特徵以你的 Windows 帳號加密存在本機，不存照片。"
            if book.persistent
            else "只存在記憶體，關閉程式就清除。"
        )
        layout.addWidget(_muted(storage))
        self.refresh()

    def refresh(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for person in self._book.people:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            name = QLabel(person.name)
            name.setToolTip(f"已記住 {len(person.samples)} 個臉部樣本")
            level = QComboBox()
            for option in Level:
                level.addItem(LEVEL_TEXT[option], option.value)
            level.setCurrentIndex(level.findData(person.level.value))
            level.currentIndexChanged.connect(
                lambda _i, pid=person.pid, box=level: self._set_level(pid, box)
            )
            remove = QPushButton("刪除")
            remove.clicked.connect(lambda _c=False, pid=person.pid: self._remove(pid))
            line.addWidget(name, 1)
            line.addWidget(level)
            line.addWidget(remove)
            self._rows.addWidget(row)
        has_people = bool(self._book.people)
        self.empty.setVisible(not has_people)
        self.clear_button.setVisible(has_people)

    def _set_level(self, pid: str, box: QComboBox) -> None:
        self._book.set_level(pid, Level(str(box.currentData())))
        self._on_changed()

    def _remove(self, pid: str) -> None:
        self._book.remove(pid)
        self._on_changed()
        self.refresh()

    def _clear_all(self) -> None:
        if self.clear_button.text() != "再按一次確認刪除":  # two-step: no accidental wipe
            self.clear_button.setText("再按一次確認刪除")
            self._confirm_timer.start()
            return
        self._confirm_timer.stop()
        self.clear_button.setText("全部刪除")
        self._book.clear()
        self._on_changed()
        self.refresh()
