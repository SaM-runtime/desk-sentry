"""Small accessible Qt controls for the spec-defined settings and toolbar."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QHideEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from desk_sentry.sentry import Status
from desk_sentry.ui import motion, theme
from desk_sentry.ui.focus import install_focus_tracking


class SettingsWheelGuard(QObject):
    """Route wheels over settings editors to their page, including later children."""

    def __init__(self, root: QWidget, scroll: QScrollArea) -> None:
        super().__init__(root)
        self._root = root
        self._scroll = scroll
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not isinstance(event, QWheelEvent) or not isinstance(watched, QWidget):
            return False
        cursor: QWidget | None = watched
        editor = False
        while cursor is not None and cursor is not self._root:
            editor = editor or isinstance(
                cursor,
                (
                    QComboBox,
                    QSpinBox,
                    SegmentedControl,
                    QTimeEdit,
                    QKeySequenceEdit,
                    QAbstractSlider,
                ),
            )
            cursor = cursor.parentWidget()
        if cursor is not self._root or not editor:
            return False
        viewport = self._scroll.viewport()
        forwarded = QWheelEvent(
            QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
            event.globalPosition(),
            event.pixelDelta(),
            event.angleDelta(),
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
        )
        QApplication.sendEvent(viewport, forwarded)
        event.accept()
        return True


class FlowLayout(QLayout):
    """Compact wrapping layout used by the selected-program chips."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(theme.D["target_chip_flow_gap"])

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        size.setWidth(min(size.width(), theme.D["target_chip_max_width"]))
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        usable = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = usable.x()
        y = usable.y()
        line_height = 0
        gap = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height and next_x > usable.right() + 1:
                x = usable.x()
                y += line_height + gap
                next_x = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x + gap
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class _MiddleElidedLabel(QLabel):
    """Keep a full tooltip while fitting long executable names into a chip."""

    def __init__(self, text: str, maximum_width: int) -> None:
        super().__init__()
        self._full_text = text
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setMaximumWidth(maximum_width)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._update_text(maximum_width)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        self._update_text(event.size().width())
        super().resizeEvent(event)

    def _update_text(self, width: int) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideMiddle,
                max(0, width),
            )
        )


class AppChip(QWidget):
    """Selected executable chip with an accessible remove action."""

    removed = Signal(str)

    def __init__(self, exe: str) -> None:
        super().__init__()
        self.exe = exe
        self.setObjectName("targetAppChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(theme.D["target_chip_height"])
        self.setMaximumWidth(theme.D["target_chip_max_width"])
        self.setToolTip(exe)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            theme.D["target_chip_h_padding"],
            0,
            theme.D["target_chip_gap"],
            0,
        )
        layout.setSpacing(theme.D["target_chip_gap"])
        label_width = (
            theme.D["target_chip_max_width"]
            - theme.D["target_chip_h_padding"]
            - 2 * theme.D["target_chip_gap"]
            - theme.D["target_chip_remove_size"]
        )
        label = _MiddleElidedLabel(exe, label_width)
        label.setObjectName("targetAppChipText")
        layout.addWidget(label)
        self.remove_button = QPushButton("×")
        self.remove_button.setObjectName("targetAppChipRemove")
        self.remove_button.setAccessibleName(f"移除 {exe}")
        self.remove_button.setToolTip(f"移除 {exe}")
        self.remove_button.setFixedSize(
            theme.D["target_chip_remove_size"], theme.D["target_chip_remove_size"]
        )
        self.remove_button.clicked.connect(lambda: self.removed.emit(self.exe))
        layout.addWidget(self.remove_button)
        self.setStyleSheet(
            f"QWidget#targetAppChip {{ background: {theme.RAISED_HOVER};"
            f" border-radius: {theme.RADIUS['chip']}px; }}"
            f"QLabel#targetAppChipText {{ font-family: {theme.MONO_FAMILY};"
            f" font-size: {theme.FONT_SIZES['small']}px; }}"
            "QPushButton#targetAppChipRemove { background: transparent; border: none;"
            " padding: 0; min-height: 0; }"
            f"QPushButton#targetAppChipRemove:hover {{ background: {theme.RAISED_PRESSED};"
            f" border-radius: {theme.RADIUS['chip']}px; }}"
            "QPushButton#targetAppChipRemove:focus[keyboardFocus='true'] {"
            f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
        )


class DropDown(QComboBox):
    """Native popup/keyboard semantics with the shared path chevron."""

    def __init__(self) -> None:
        super().__init__()
        install_focus_tracking()
        self.setStyleSheet("QComboBox::drop-down { border: none; width: 0; }")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        size = theme.D["dropdown_chevron"]
        inset = theme.SPACE_8
        theme.glyph_icon("chevron", theme.TEXT_2, size).paint(
            p, self.width() - size - inset, (self.height() - size) // 2, size, size
        )
        p.end()


def _focus(p: QPainter, widget: QWidget, radius: int) -> None:
    if widget.hasFocus() and widget.property("keyboardFocus"):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT), theme.D["focus_width"]))
        p.drawRoundedRect(QRectF(widget.rect()).adjusted(1, 1, -1, -1), radius, radius)


class Switch(QCheckBox):
    """QCheckBox semantics and keyboard access with a 36 by 20 switch painting."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        install_focus_tracking()
        self.setAccessibleName(text)
        self.setFixedSize(theme.D["switch_width"], theme.D["switch_height"])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(theme.MOTION["switch"])
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._move_knob)
        self.toggled.connect(self._toggle)

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def _move_knob(self, value: float) -> None:
        if not motion.animations_enabled():
            self._animation.stop()
            value = float(self.isChecked())
        self._position = value
        self.update()

    def _toggle(self, checked: bool) -> None:
        self._animation.stop()
        if not motion.animations_enabled() or not self.isVisible():
            self._move_knob(float(checked))
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(float(checked))
        self._animation.start()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._animation.stop()
        self._position = float(self.isChecked())
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from PySide6.QtGui import QColor

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme.ACCENT if self.isChecked() else theme.BORDER_STRONG)
        if self.underMouse():
            color = color.lighter(106)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        radius = theme.D["switch_height"] / 2
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        inset, knob = theme.D["switch_inset"], theme.D["switch_knob"]
        x = inset + (self.width() - knob - 2 * inset) * self._position
        p.setBrush(QColor(theme.WHITE if self.isChecked() else theme.SWITCH_OFF))
        p.drawEllipse(QRectF(x, inset, knob, knob))
        _focus(p, self, theme.RADIUS["control"])
        p.end()


class SegmentedControl(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, items: list[tuple[str, str]], value: str) -> None:
        super().__init__()
        install_focus_tracking()
        self._items = items
        self._index = max(0, self.findData(value))
        self._position = float(self._index)
        self.setFixedHeight(theme.D["control_height"])
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(theme.MOTION["segment"])
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._move_selection)
        layout = QHBoxLayout(self)
        inset = theme.D["switch_inset"]
        layout.setContentsMargins(inset, inset, inset, inset)
        layout.setSpacing(0)
        self.buttons: list[QPushButton] = []
        for index, (_, label) in enumerate(items):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
            layout.addWidget(button, 1)
            self.buttons.append(button)
        self._style_buttons()

    def findData(self, value: str) -> int:  # noqa: N802
        return next((i for i, item in enumerate(self._items) if item[0] == value), -1)

    def currentData(self) -> str:  # noqa: N802
        return self._items[self._index][0]

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if index < 0 or index >= len(self._items):
            return
        if index == self._index:
            self._style_buttons()
            return
        self._index = index
        self._style_buttons()
        self._animation.stop()
        if motion.animations_enabled() and self.isVisible():
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(float(index))
            self._animation.start()
        else:
            self._move_selection(float(index))
        self.currentIndexChanged.emit(index)

    def _style_buttons(self) -> None:
        for i, button in enumerate(self.buttons):
            button.setChecked(i == self._index)
            color = theme.TEXT if i == self._index else theme.TEXT_2
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {color}; padding: 0;"
                f" min-height: 0; border-radius: {theme.RADIUS['chip']}px; }}"
                "QPushButton:focus[keyboardFocus='true'] {"
                f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
            )

    def _move_selection(self, value: float) -> None:
        if not motion.animations_enabled():
            self._animation.stop()
            value = float(self._index)
        self._position = value
        self.update()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._animation.stop()
        self._position = float(self._index)
        super().hideEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from PySide6.QtGui import QColor

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.SUNKEN))
        radius = theme.RADIUS["control"]
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        inset = theme.D["switch_inset"]
        width = (self.width() - 2 * inset) / len(self._items)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.RAISED_HOVER))
        selected = QRectF(inset + width * self._position, inset, width, self.height() - 2 * inset)
        p.drawRoundedRect(selected, theme.RADIUS["chip"], theme.RADIUS["chip"])
        p.end()


class Stepper(QWidget):
    valueChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        install_focus_tracking()
        self.setFixedSize(theme.D["stepper_width"], theme.D["control_height"])
        self.spin = QSpinBox(self)
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setStyleSheet(
            "QSpinBox { background: transparent; border: none; padding: 0; }"
            "QSpinBox:focus[keyboardFocus='true'] {"
            f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
        )
        self.spin.valueChanged.connect(self.valueChanged)
        self.minus = QPushButton("−", self)
        self.plus = QPushButton("+", self)
        for button in (self.minus, self.plus):
            button.setStyleSheet(
                f"QPushButton {{ background: transparent; padding: 0; min-height: 0; }}"
                f"QPushButton:hover {{ background: {theme.RAISED_HOVER}; }}"
                "QPushButton:focus[keyboardFocus='true'] {"
                f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
            )
            button.setAutoRepeat(True)
        self.minus.clicked.connect(self.spin.stepDown)
        self.plus.clicked.connect(self.spin.stepUp)
        self.setFocusProxy(self.spin)

    def setRange(self, low: int, high: int) -> None:  # noqa: N802
        self.spin.setRange(low, high)

    def setValue(self, value: int) -> None:  # noqa: N802
        self.spin.setValue(value)

    def value(self) -> int:
        return self.spin.value()

    def setSuffix(self, suffix: str) -> None:  # noqa: N802
        self.spin.setSuffix(suffix)

    def setSpecialValueText(self, text: str) -> None:  # noqa: N802
        self.spin.setSpecialValueText(text)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        button_width = theme.D["stepper_button"]
        value_width = theme.D["stepper_value_width"]
        self.minus.setGeometry(0, 0, button_width, self.height())
        self.spin.setGeometry(button_width, 0, value_width, self.height())
        self.plus.setGeometry(button_width + value_width, 0, button_width, self.height())
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from PySide6.QtGui import QColor

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.RAISED))
        r = theme.RADIUS["control"]
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.end()


class IconButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str, size: int = theme.D["icon_button"]) -> None:
        super().__init__()
        install_focus_tracking()
        self._icon_name = icon_name
        self._badge = 0
        self.setFixedSize(size, size)
        self.setAccessibleName(tooltip)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; padding: 0; min-height: 0;"
            f" border-radius: {theme.RADIUS['control']}px; }}"
            f"QPushButton:hover {{ background: {theme.RAISED_HOVER}; }}"
            f"QPushButton:pressed {{ background: {theme.RAISED_PRESSED}; }}"
            "QPushButton:focus[keyboardFocus='true'] {"
            f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
        )
        self._set_icon(False)

    def _set_icon(self, hover: bool) -> None:
        self.setIcon(theme.glyph_icon(self._icon_name, theme.TEXT if hover else theme.TEXT_2))
        self.setIconSize(QSize(theme.D["icon"], theme.D["icon"]))

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._set_icon(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._set_icon(False)
        super().leaveEvent(event)

    def set_badge(self, count: int) -> None:
        self._badge = count
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QFont

        super().paintEvent(event)
        if not self._badge:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.font()
        font.setPixelSize(theme.FONT_SIZES["caption"])
        font.setWeight(QFont.Weight.Bold)
        p.setFont(font)
        size = theme.D["badge_min"]
        width = max(size, p.fontMetrics().horizontalAdvance(str(self._badge)) + theme.SPACE_8)
        rect = QRectF(self.width() - width, 0, width, size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(rect, size / 2, size / 2)
        p.setPen(QColor(theme.ON_ACCENT))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self._badge))
        p.end()


class ArmButton(QPushButton):
    def __init__(self) -> None:
        super().__init__()
        install_focus_tracking()
        self._armed = True
        self._automatic_pause = False
        self._status = Status.CLEAR
        self._status_color = QColor(theme.STATUS_COLOR[self._status])
        self._color_animation = QVariantAnimation(self)
        self._color_animation.setDuration(theme.MOTION["color"])
        self._color_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._color_animation.valueChanged.connect(self._set_status_color)
        self.setFixedHeight(theme.D["arm_height"])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_armed(True)

    def set_armed(self, armed: bool) -> None:
        self._armed = armed
        self.setText("監看中" if armed else "開始監看")
        self.setAccessibleName("暫停監看" if armed else "開始監看")
        pad = theme.D["arm_padding"]
        dot = theme.D["dot"] + theme.SPACE_8 if armed else 0
        width = self.fontMetrics().horizontalAdvance("暫停監看") + 2 * pad + dot
        self.setFixedWidth(width)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")
        self.update()

    def set_status(self, status: Status) -> None:
        changed = status != self._status
        self._status = status
        target = QColor(theme.STATUS_COLOR[status])
        if not motion.animations_enabled() or not self.isVisible():
            self._color_animation.stop()
            self._set_status_color(target)
        elif changed:
            self._color_animation.stop()
            self._color_animation.setStartValue(self._status_color)
            self._color_animation.setEndValue(target)
            self._color_animation.start()

    def set_automatic_pause(self, paused: bool) -> None:
        self._automatic_pause = paused
        if paused:
            self.setText("自動暫停")
            self.set_status(Status.PAUSED)
        else:
            self.setText("監看中" if self._armed else "開始監看")
        self.setAccessibleDescription("自動暫停" if paused else "")
        self.update()

    def _set_status_color(self, value: QColor) -> None:
        self._status_color = QColor(value)
        self.update()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._color_animation.stop()
        self._status_color = QColor(theme.STATUS_COLOR[self._status])
        super().hideEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        if self._armed and not self._automatic_pause:
            self.setText("暫停監看")
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._armed and not self._automatic_pause:
            self.setText("監看中")
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QFont

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = (
            theme.RAISED
            if self._armed
            else theme.ACCENT_HOVER
            if self.underMouse()
            else theme.ACCENT
        )
        p.setBrush(QColor(fill))
        p.setPen(QPen(QColor(theme.BORDER), 1) if self._armed else Qt.PenStyle.NoPen)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme.D["arm_height"] / 2
        p.drawRoundedRect(rect, radius, radius)
        font = self.font()
        font.setWeight(QFont.Weight.Medium if self._armed else QFont.Weight.DemiBold)
        p.setFont(font)
        pad = theme.D["arm_padding"]
        if self._armed:
            dot = theme.D["dot"]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._status_color)
            p.drawEllipse(QRectF(pad, (self.height() - dot) / 2, dot, dot))
            rect.adjust(dot + theme.SPACE_8, 0, 0, 0)
        p.setPen(QColor(theme.TEXT if self._armed else theme.ON_ACCENT))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        _focus(p, self, int(radius))
        p.end()
