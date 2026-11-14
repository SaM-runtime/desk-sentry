"""Main window: a glanceable live mirror with a responsive settings panel."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from threading import Thread

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from desk_sentry.build_info import get_build_info
from desk_sentry.config import MAX_TARGET_APPS, Settings
from desk_sentry.detector import Box
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.hotkey import conflicts
from desk_sentry.hotkey import parse as parse_hotkey
from desk_sentry.people import LEVEL_TEXT, Level, PeopleBook
from desk_sentry.schedule import next_boundary
from desk_sentry.sentry import Status, zone_for_point
from desk_sentry.ui import motion, theme
from desk_sentry.ui.controls import (
    AppChip,
    ArmButton,
    DropDown,
    FlowLayout,
    IconButton,
    SegmentedControl,
    SettingsWheelGuard,
    Stepper,
    Switch,
)
from desk_sentry.ui.discreet import ScreenCatalog, ScreenMap, same_screen
from desk_sentry.ui.mirror_view import MirrorView
from desk_sentry.ui.people import PeopleEditor

SettingsCallback = Callable[[Settings], None]
AppLister = Callable[[], list[tuple[str, str]]]  # (exe, example window title)
CameraLister = Callable[[], list[object]]

_SENSITIVITY = [("low", "低"), ("medium", "中"), ("high", "高")]
_CAMERA_MODES = [("auto", "自動（建議）"), ("exclusive", "相容模式（獨占）")]
_AUTOMATIC_PAUSE_REASONS = ("locked", "schedule", "meeting", "manual_yield")
_SENSITIVITY_HINT = {
    "low": "約 3 公尺內有人移動才提醒",
    "medium": "約 5 公尺內有人移動就提醒（建議）",
    "high": "約 7 公尺內，遠一點也提醒",
}
_ACTIONS = [
    ("none", "不做任何事"),
    ("minimize_app", "縮小指定程式"),
    ("minimize", "最小化所有視窗"),
    ("lock", "鎖定電腦"),
    ("curtain", "蓋上黑幕"),
]
_AWAY_ACTIONS = [
    ("none", "不做任何事"),
    ("lock", "鎖定電腦"),
    ("minimize_app", "縮小目標程式"),
]
_HIDE_HOTKEY_STYLES = [("minimize", "縮小目標程式"), ("curtain", "蓋上黑幕")]
_DISCREET_CUES = [
    ("dot", "角落小點"),
    ("line", "螢幕邊緣細線"),
    ("none", "不顯示（只執行警示動作）"),
]
_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
_UNKNOWN_LEVELS = [("high", LEVEL_TEXT[Level.HIGH]), ("medium", LEVEL_TEXT[Level.MEDIUM])]
_CORNERS = [
    ("top-right", "右上"),
    ("top-left", "左上"),
    ("bottom-right", "右下"),
    ("bottom-left", "左下"),
]


def _combo(items: list[tuple[str, str]], value: str) -> QComboBox:
    combo = DropDown()
    combo.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
    for key, label in items:
        combo.addItem(label, key)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    return combo


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("sectionTitle")
    return label


def _hint(text: str) -> QLabel:
    note = QLabel(text)
    note.setObjectName("settingHint")
    note.setWordWrap(True)
    return note


def _required_hint(row: QWidget) -> QLabel:
    hint = row.findChild(QLabel, "settingHint")
    if hint is None:
        raise RuntimeError("settings row is missing its hint label")
    return hint


def _divider() -> QFrame:
    divider = QFrame()
    divider.setObjectName("rowDivider")
    divider.setFixedHeight(theme.D["divider_height"])
    return divider


def _setting_row(
    label: str,
    widget: QWidget,
    hint: str | None = None,
    *,
    dependent: bool = False,
    stacked: bool = False,
) -> QWidget:
    if label and not widget.accessibleName():
        widget.setAccessibleName(label)
    row = QWidget()
    row.setObjectName("dependentSettingRow" if dependent else "settingRow")
    row.setMinimumHeight(theme.D["row_min_height"])
    outer = QHBoxLayout(row)
    left_margin = theme.D["row_h_padding"] + (theme.D["space_16"] if dependent else 0)
    outer.setContentsMargins(
        left_margin,
        theme.D["row_v_padding"],
        theme.D["row_h_padding"],
        theme.D["row_v_padding"],
    )
    outer.setSpacing(theme.D["space_8"])
    if dependent:
        marker = QFrame()
        marker.setObjectName("dependentMarker")
        marker.setFixedSize(theme.D["dependent_marker_width"], theme.D["dependent_marker_height"])
        outer.addWidget(marker, 0, Qt.AlignmentFlag.AlignVCenter)
    copy = QVBoxLayout()
    copy.setContentsMargins(0, 0, 0, 0)
    copy.setSpacing(theme.D["space_4"])
    title = QLabel(label)
    title.setObjectName("settingTitle")
    copy.addWidget(title)
    if hint:
        copy.addWidget(_hint(hint))
    if stacked:
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.D["space_8"])
        column.addLayout(copy)
        column.addWidget(widget)
        outer.addLayout(column, 1)
    else:
        outer.addLayout(copy, 1)
        outer.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class _SettingsCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("settingsCard")
        self._content = QVBoxLayout(self)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(0)

    def add_row(self, row: QWidget) -> QWidget:
        if self._content.count():
            self._content.addWidget(_divider())
        self._content.addWidget(row)
        return row


class _CameraDropDown(DropDown):
    """Camera picker that refreshes when its popup is opened."""

    popup_shown = Signal()

    def showPopup(self) -> None:  # noqa: N802 - Qt API
        self.popup_shown.emit()
        super().showPopup()


class _Scrim(QWidget):
    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("drawerScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        self.clicked.emit()
        super().mousePressEvent(event)  # type: ignore[arg-type]


class _ElidedLabel(QLabel):
    """Single-line label that can shrink without raising the window minimum width."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._full_text = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full_text), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text
        self._refresh_text()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        QLabel.setText(self, shown)


class _MainMirrorView(MirrorView):
    """Main-stage pause presentation with automatic pauses kept non-interactive."""

    def __init__(self, *, radius: float, badge: bool, align_top: bool) -> None:
        super().__init__(radius=radius, badge=badge, align_top=align_top)
        self._pause_detail: str | None = None
        self._discreet = False

    @property
    def pause_detail(self) -> str | None:
        return self._pause_detail

    @property
    def resume_rect(self) -> QRectF:
        return QRectF(self._resume_rect)

    def set_automatic_pause_detail(self, detail: str | None) -> None:
        self._pause_detail = detail
        if detail is not None:
            self._resume_rect = QRectF()
        self.update()

    def set_discreet(self, enabled: bool) -> None:
        self._discreet = bool(enabled)
        if self._discreet:
            self._clear_private_content()
            self.setAccessibleName("偵測中 · 畫面已隱藏")
        else:
            self.setAccessibleName("")
        self._sync_breathing()
        self.update()

    def set_snapshot(self, snapshot: Snapshot) -> None:
        if self._discreet:
            snapshot = replace(
                snapshot,
                frame=None,
                tracks=(),
                self_zone=None,
                alert_thumbnail=None,
                alert_face=None,
            )
            self._clear_private_content()
        super().set_snapshot(snapshot)

    def _clear_private_content(self) -> None:
        self._image = None
        for state in self._tracks.values():
            state.position.stop()
            state.fade.stop()
        self._tracks.clear()
        self._painted_chip_rects.clear()
        self._snapshot = replace(
            self._snapshot,
            frame=None,
            tracks=(),
            self_zone=None,
            alert_thumbnail=None,
            alert_face=None,
        )

    def _draw_no_image(self, painter: QPainter, outer: QRectF) -> None:
        if self._discreet:
            self._draw_discreet(painter, outer)
            return
        if self._snapshot.status is not Status.PAUSED or self._pause_detail is None:
            super()._draw_no_image(painter, outer)
            return

        total_height = (
            theme.NO_IMAGE_ICON
            + theme.SPACE_12
            + theme.NO_IMAGE_TITLE_HEIGHT
            + theme.SPACE_6
            + theme.NO_IMAGE_DETAIL_HEIGHT
        )
        top = outer.center().y() - total_height / 2
        icon_size = theme.NO_IMAGE_ICON
        icon = QRectF(outer.center().x() - icon_size / 2, top, icon_size, icon_size)
        theme.glyph_icon("mirror", theme.TEXT_3, icon_size).paint(painter, icon.toRect())

        title_rect = QRectF(
            outer.left(),
            icon.bottom() + theme.SPACE_12,
            outer.width(),
            theme.NO_IMAGE_TITLE_HEIGHT,
        )
        font = QFont(self.font())
        font.setPixelSize(theme.FONT_STATUS_PX)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "已暫停")

        detail_padding = theme.SPACING_16
        detail_rect = QRectF(
            outer.left() + detail_padding,
            title_rect.bottom() + theme.SPACE_6,
            outer.width() - 2 * detail_padding,
            theme.NO_IMAGE_DETAIL_HEIGHT,
        )
        font.setPixelSize(theme.FONT_BODY_PX)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_2))
        painter.drawText(detail_rect, Qt.AlignmentFlag.AlignCenter, self._pause_detail)
        self._resume_rect = QRectF()

    def _draw_discreet(self, painter: QPainter, outer: QRectF) -> None:
        icon_size = theme.NO_IMAGE_ICON
        text = "偵測中 · 畫面已隱藏"
        total_height = icon_size + theme.SPACE_12 + theme.NO_IMAGE_TITLE_HEIGHT
        top = outer.center().y() - total_height / 2
        icon = QRectF(outer.center().x() - icon_size / 2, top, icon_size, icon_size)
        theme.app_icon().paint(painter, icon.toRect())

        font = QFont(self.font())
        font.setPixelSize(theme.FONT_BODY_PX)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text_width = painter.fontMetrics().horizontalAdvance(text)
        dot_size = theme.D["dot"]
        gap = theme.SPACE_6
        group_width = dot_size + gap + text_width
        text_rect = QRectF(
            outer.center().x() - group_width / 2 + dot_size + gap,
            icon.bottom() + theme.SPACE_12,
            text_width,
            theme.NO_IMAGE_TITLE_HEIGHT,
        )
        dot = QRectF(
            text_rect.left() - gap - dot_size,
            text_rect.center().y() - dot_size / 2,
            dot_size,
            dot_size,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.STATUS_COLOR[self._snapshot.status]))
        painter.drawEllipse(dot)
        painter.setPen(QColor(theme.TEXT_2))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)
        self._resume_rect = QRectF()


class MainWindow(QMainWindow):
    _camera_list_ready = Signal(int, object)

    def __init__(
        self,
        settings: Settings,
        on_settings: SettingsCallback,
        *,
        autostart: tuple[Callable[[], bool], Callable[[bool], bool]] | None = None,
        list_apps: AppLister | None = None,
        people: PeopleBook | None = None,
        display_version: str | None = None,
        screen_catalog: ScreenCatalog | None = None,
        list_cameras: CameraLister | None = None,
    ) -> None:
        super().__init__()
        self._autostart = autostart
        self._list_apps = list_apps or (lambda: [])
        self._list_cameras = list_cameras or self._default_camera_lister
        self._camera_list_generation = 0
        self._camera_retry_timer = QTimer(self)
        self._camera_retry_timer.setInterval(10_000)
        self._camera_retry_timer.timeout.connect(
            lambda: self._request_camera_list(show_loading=False)
        )
        self._camera_diagnostics = ""
        self._camera_status_presentation: tuple[str, str] | None = None
        self._camera_list_ready.connect(self._apply_camera_list)
        self._people = people
        self.screen_catalog = screen_catalog or ScreenCatalog()
        if screen_catalog is None:
            self.screen_catalog.setParent(self)
        self.on_preview_cue: Callable[[bool], None] | None = None
        self.on_people_changed: Callable[[], None] | None = None
        self.on_check_updates: Callable[[], None] | None = None
        self.on_open_update: Callable[[], None] | None = None
        self.on_skip_update: Callable[[], None] | None = None
        self._update_checking = False
        self._display_version = display_version or get_build_info().display_version
        self._settings = settings
        self._on_settings = on_settings
        self.allow_close = False
        self._last_tracks: tuple[TrackView, ...] = ()
        self._has_frame = False
        self._initial_focus_set = False
        self.on_hide_to_tray: Callable[[], None] | None = None
        self.on_open_diagnostics: Callable[[], None] | None = None
        self.on_compact: Callable[[], None] | None = None
        self.on_history: Callable[[], None] | None = None
        self.on_hotkey: Callable[[str], bool] | None = None
        self.on_hide_hotkey: Callable[[str], bool] | None = None
        self.on_mark_hotkey: Callable[[str, str], bool] | None = None
        self._pause_reason: str | None = None
        self._drawer_open = False
        self._narrow = False
        self._drawer_animation: QParallelAnimationGroup | None = None

        self._last_status = Status.PAUSED
        self.setWindowTitle("Desk Sentry 後照鏡")
        self.setWindowIcon(theme.app_icon())
        self.resize(theme.D["main_default_width"], theme.D["main_default_height"])
        self.setMinimumSize(theme.D["main_min_width"], theme.D["main_min_height"])
        self._root = QWidget()
        self._root.setObjectName("root")
        self._root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCentralWidget(self._root)
        self._outer = QHBoxLayout(self._root)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._main = self._build_main()
        self._panel = self._build_panel()
        self._outer.addWidget(self._main, 1)
        self._outer.addWidget(self._panel)
        self._scrim = _Scrim(self._root)
        self._scrim.clicked.connect(self.close_drawer)
        self._scrim_effect = QGraphicsOpacityEffect(self._scrim)
        self._scrim.setGraphicsEffect(self._scrim_effect)
        self._scrim.hide()
        self._render_armed()
        self._sync_discreet_ui()
        self._sync_responsive_layout(force=True)
        self._request_camera_list()

    # ---------------------------------------------------------------- layout
    def _build_main(self) -> QWidget:
        main = QWidget()
        main.setObjectName("mainContent")
        layout = QVBoxLayout(main)
        layout.setContentsMargins(
            theme.D["main_h_padding"],
            theme.D["main_v_padding"],
            theme.D["main_h_padding"],
            theme.D["main_v_padding"],
        )
        layout.setSpacing(0)
        toolbar = QWidget()
        toolbar.setFixedHeight(theme.D["toolbar_height"])
        header = QHBoxLayout(toolbar)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(theme.D["toolbar_gap"])
        self._app_icon = QLabel()
        self._app_icon.setPixmap(
            theme.app_icon().pixmap(theme.D["toolbar_icon"], theme.D["toolbar_icon"])
        )
        self._app_icon.setFixedSize(theme.D["toolbar_icon"], theme.D["toolbar_icon"])
        header.addWidget(self._app_icon)
        self._toolbar_title = _ElidedLabel("Desk Sentry")
        self._toolbar_title.setObjectName("windowTitle")
        self._toolbar_title.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        header.addWidget(self._toolbar_title)
        header.addStretch(1)
        self.history_button = IconButton("history", "最近提醒", size=theme.D["icon_button"])
        self.history_button.clicked.connect(lambda: self.on_history() if self.on_history else None)
        header.addWidget(self.history_button)
        self.compact_button = IconButton("pip", "小鏡子模式", size=theme.D["icon_button"])
        self.compact_button.clicked.connect(lambda: self.on_compact() if self.on_compact else None)
        header.addWidget(self.compact_button)
        self.settings_button = IconButton("settings", "設定", size=theme.D["icon_button"])
        self.settings_button.clicked.connect(self._toggle_drawer)
        header.addWidget(self.settings_button)
        self.arm_button = ArmButton()
        self.arm_button.clicked.connect(self.toggle_armed)
        header.addWidget(self.arm_button)
        layout.addWidget(toolbar)
        layout.addSpacing(theme.D["space_16"])

        # MirrorView owns the visible status pill and the centered no-image state.
        # Keep these labels as hidden compatibility surfaces for existing tests.
        self._dot = QLabel()
        self._dot.hide()
        self._status_title = QLabel()
        self._status_title.hide()
        self._status_detail = QLabel()
        self._status_detail.hide()
        self.mirror = _MainMirrorView(radius=theme.D["stage_radius"], badge=True, align_top=False)
        self.mirror.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mirror.setToolTip("點一下畫面中的你，指定為「自己」")
        self.mirror.picked.connect(self._pick_self)
        resume = getattr(self.mirror, "resume_requested", None)
        if resume is not None:
            resume.connect(self._resume_if_manual)
        layout.addWidget(self.mirror, 1)
        self.pause_reason = QLabel()
        self.pause_reason.setObjectName("pauseReason")
        self.pause_reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pause_reason.setStyleSheet(
            f"color: {theme.TEXT_2}; font-size: {theme.FONT_SIZES['small']}px;"
            f" padding-top: {theme.SPACING_8}px;"
        )
        self.pause_reason.hide()
        layout.addWidget(self.pause_reason)
        self._info_row = QWidget()
        info = self._info_row
        info.setFixedHeight(theme.D["stats_height"] + theme.D["space_12"])
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(0, theme.D["space_12"], 0, 0)
        info_layout.setSpacing(theme.D["space_4"])
        self._people_stats = _ElidedLabel("畫面中沒有其他人")
        self._people_stats.setObjectName("peopleStats")
        info_layout.addWidget(self._people_stats)
        info_hint = _ElidedLabel("  ·  點畫面中的你可重新指定座位")
        info_hint.setObjectName("statsHint")
        info_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info_layout.addWidget(info_hint, 1)
        self._stats = QLabel("—")
        self._stats.setObjectName("stats")
        self._stats.setVisible(self._settings.diagnostics)
        info_layout.addWidget(self._stats)
        layout.addWidget(info)
        self._render_status(Snapshot(Status.PAUSED))
        return main

    def _build_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(theme.D["settings_width"])
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsContent")
        content.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._settings_content = content
        form = QVBoxLayout(content)
        padding = theme.D["space_20"]
        form.setContentsMargins(padding, padding, padding, padding)
        form.setSpacing(0)
        s = self._settings

        def add_section(title: str, card: _SettingsCard, *, first: bool = False) -> None:
            form.addSpacing(theme.D["section_first_top"] if first else theme.D["section_gap"])
            form.addWidget(_section(title))
            form.addSpacing(theme.D["space_8"])
            form.addWidget(card)

        discreet = _SettingsCard()
        self.discreet = Switch()
        self.discreet.setChecked(s.discreet)
        self.discreet.toggled.connect(self._discreet_changed)
        discreet.add_row(
            _setting_row(
                "低調模式",
                self.discreet,
                "偵測照常，不顯示畫面，改用低調提示",
            )
        )
        self.discreet_cue = _combo(_DISCREET_CUES, s.discreet_cue)
        self.discreet_cue.currentIndexChanged.connect(self._discreet_cue_changed)
        self.discreet_cue_row = discreet.add_row(
            _setting_row("提醒方式", self.discreet_cue, dependent=True)
        )
        self.discreet_corner = _combo(_CORNERS, s.discreet_corner)
        self.discreet_corner.currentIndexChanged.connect(
            lambda _: self._update(discreet_corner=str(self.discreet_corner.currentData()))
        )
        self.discreet_corner_row = discreet.add_row(
            _setting_row("小點位置", self.discreet_corner, dependent=True)
        )
        self.discreet_screen_mode = _combo(
            [("all", "全部螢幕"), ("primary", "主要螢幕"), ("selected", "自選")],
            s.discreet_screen_mode,
        )
        self.discreet_screen_mode.currentIndexChanged.connect(
            lambda _: self._update(
                discreet_screen_mode=str(self.discreet_screen_mode.currentData())
            )
        )
        self.discreet_screen_row = discreet.add_row(
            _setting_row("顯示螢幕", self.discreet_screen_mode, dependent=True)
        )
        self.discreet_screen_detail = QWidget()
        detail = QVBoxLayout(self.discreet_screen_detail)
        detail.setContentsMargins(
            theme.D["row_h_padding"] + theme.D["space_16"],
            0,
            theme.D["row_h_padding"],
            theme.D["row_v_padding"],
        )
        detail.setSpacing(theme.D["space_8"])
        self.screen_map = ScreenMap(self.screen_catalog)
        self.screen_map.screen_clicked.connect(self._screen_clicked)
        detail.addWidget(self.screen_map)
        screen_footer = QHBoxLayout()
        self.screen_selection_hint = _hint("")
        self.screen_selection_hint.setWordWrap(True)
        screen_footer.addWidget(self.screen_selection_hint, 1)
        self.identify_screens = QPushButton("顯示位置")
        self.identify_screens.setObjectName("secondaryButton")
        self.identify_screens.setFixedHeight(theme.D["control_height"])
        self.identify_screens.clicked.connect(
            lambda: self.on_preview_cue(True) if self.on_preview_cue else None
        )
        screen_footer.addWidget(self.identify_screens)
        detail.addLayout(screen_footer)
        self.offline_screens = QWidget()
        self.offline_screen_layout = FlowLayout(self.offline_screens)
        detail.addWidget(self.offline_screens)
        discreet.add_row(self.discreet_screen_detail)
        self.screen_catalog.changed.connect(self._refresh_screen_ui)
        add_section("低調模式", discreet, first=True)

        detection = _SettingsCard()
        self.sensitivity = SegmentedControl(_SENSITIVITY, s.sensitivity)
        self._sensitivity_hint = _hint(_SENSITIVITY_HINT[s.sensitivity])
        sensitivity_box = QWidget()
        sensitivity_layout = QVBoxLayout(sensitivity_box)
        sensitivity_layout.setContentsMargins(0, 0, 0, 0)
        sensitivity_layout.setSpacing(theme.D["space_4"])
        sensitivity_layout.addWidget(self.sensitivity)
        sensitivity_layout.addWidget(self._sensitivity_hint)
        self.sensitivity.currentIndexChanged.connect(self._sensitivity_changed)
        detection.add_row(_setting_row("靈敏度", sensitivity_box, stacked=True))
        self.far_passersby = Switch()
        self.far_passersby.setChecked(s.alert_far_passersby)
        self.far_passersby.toggled.connect(
            lambda value: self._update(alert_far_passersby=bool(value))
        )
        detection.add_row(
            _setting_row("遠處經過的人也提醒", self.far_passersby, "預設只標示，走近才提醒")
        )
        self.still = Stepper()
        self.still.setRange(0, 600)
        self.still.setSuffix(" 秒")
        self.still.setSpecialValueText("永不")
        self.still.setValue(round(s.stationary_mute_seconds))
        self.still.valueChanged.connect(
            lambda value: self._update(stationary_mute_seconds=float(value))
        )
        detection.add_row(_setting_row("靜止多久後不再提醒", self.still, "例如坐在你後面的同事"))
        self.camera = _CameraDropDown()
        self.camera.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
        self.camera.addItem("讀取中…", None)
        self.camera.setEnabled(False)
        self.camera.activated.connect(self._camera_chosen)
        self.camera.popup_shown.connect(lambda: self._request_camera_list(show_loading=False))
        detection.add_row(_setting_row("攝影機", self.camera, "換攝影機後會自動重新連線"))
        self.camera_mode = _combo(_CAMERA_MODES, s.camera_mode)
        self.camera_mode.currentIndexChanged.connect(
            lambda _index: self._update(camera_mode=str(self.camera_mode.currentData()))
        )
        detection.add_row(
            _setting_row(
                "攝影機模式",
                self.camera_mode,
                "自動模式會先嘗試與視訊會議共用攝影機",
            )
        )
        camera_status_box = QWidget()
        camera_status_layout = QHBoxLayout(camera_status_box)
        camera_status_layout.setContentsMargins(0, 0, 0, 0)
        camera_status_layout.setSpacing(theme.D["space_8"])
        self.camera_status_dot = QLabel()
        self.camera_status_dot.setFixedSize(theme.D["dot"], theme.D["dot"])
        camera_status_layout.addWidget(self.camera_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.camera_status = QLabel()
        self.camera_status.setObjectName("cameraStatus")
        self.camera_status.setWordWrap(True)
        self.camera_status.setMinimumWidth(0)
        self.camera_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        camera_status_layout.addWidget(self.camera_status, 1)
        self.copy_camera_diagnostics = QPushButton("複製診斷資訊")
        self.copy_camera_diagnostics.setObjectName("secondaryButton")
        self.copy_camera_diagnostics.setFixedHeight(theme.D["control_height"])
        self.copy_camera_diagnostics.setEnabled(False)
        self.copy_camera_diagnostics.clicked.connect(self._copy_camera_diagnostics)
        camera_status_layout.addWidget(self.copy_camera_diagnostics)
        detection.add_row(_setting_row("目前狀態", camera_status_box, stacked=True))
        self.set_camera_status("pending")
        self.auto_yield_camera = Switch()
        self.auto_yield_camera.setChecked(s.auto_yield_camera)
        self.auto_yield_camera.toggled.connect(
            lambda value: self._update(auto_yield_camera=bool(value))
        )
        detection.add_row(
            _setting_row(
                "開會時自動讓出攝影機",
                self.auto_yield_camera,
                "只有獨占攝影機時才會暫停監看",
            )
        )
        add_section("偵測", detection)

        yourself = _SettingsCard()
        self.exclude_self = Switch()
        self.exclude_self.setChecked(s.exclude_self)
        self.exclude_self.toggled.connect(lambda value: self._update(exclude_self=bool(value)))
        yourself.add_row(_setting_row("排除坐在鏡頭前的自己", self.exclude_self))
        self.recalibrate = QPushButton("重新辨識")
        self.recalibrate.setObjectName("secondaryButton")
        self.recalibrate.setFixedHeight(theme.D["control_height"])
        self.recalibrate.setToolTip(
            "坐回平常位置後按下，約 1.5 秒後自動框出你的位置。也可以直接點預覽畫面中的自己。"
        )
        self.recalibrate.clicked.connect(lambda: self._update(self_zone=None))
        yourself.add_row(_setting_row("座位", self.recalibrate, "認錯時，直接點畫面中的你"))
        add_section("你自己", yourself)

        alerts = _SettingsCard()
        self.show_mirror = Switch()
        self.show_mirror.setChecked(s.show_mirror)
        self.show_mirror.toggled.connect(self._show_mirror_changed)
        self.show_mirror_row = alerts.add_row(
            _setting_row("彈出後照鏡", self.show_mirror, "低調模式開啟中")
        )
        self.show_mirror_notice = _required_hint(self.show_mirror_row)
        self.corner_combo = _combo(_CORNERS, s.mirror_corner)
        self.corner_combo.currentIndexChanged.connect(
            lambda _: self._update(mirror_corner=str(self.corner_combo.currentData()))
        )
        self.corner_row = alerts.add_row(_setting_row("位置", self.corner_combo, dependent=True))
        self._set_dependency(self.corner_row, s.show_mirror)
        self.edge_flash = Switch()
        self.edge_flash.setChecked(s.edge_flash)
        self.edge_flash.toggled.connect(lambda value: self._update(edge_flash=bool(value)))
        self.edge_flash_row = alerts.add_row(
            _setting_row("螢幕邊緣閃光", self.edge_flash, "低調模式開啟中")
        )
        self.edge_flash_notice = _required_hint(self.edge_flash_row)
        self.sound = Switch()
        self.sound.setChecked(s.sound)
        self.sound.toggled.connect(lambda value: self._update(sound=bool(value)))
        self.sound_row = alerts.add_row(_setting_row("提示音", self.sound, "低調模式開啟中"))
        self.sound_notice = _required_hint(self.sound_row)
        add_section("提醒方式", alerts)
        evade = _SettingsCard()
        self.action = _combo(_ACTIONS, s.action)
        self.action.currentIndexChanged.connect(self._action_changed)
        self.cover_app = DropDown()
        self.cover_app.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
        self.cover_app.activated.connect(self._cover_app_chosen)
        self.cover_app.currentIndexChanged.connect(self._sync_cover_tooltip)
        self.refresh_cover_apps = IconButton("refresh", "重新整理", size=theme.D["control_height"])
        self.refresh_cover_apps.clicked.connect(self.reload_apps)
        cover_picker = QWidget()
        cover_layout = QHBoxLayout(cover_picker)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        cover_layout.setSpacing(theme.D["space_8"])
        cover_layout.addWidget(self.cover_app)
        cover_layout.addWidget(self.refresh_cover_apps)
        self.cover_app_row = evade.add_row(
            _setting_row("掩護視窗", cover_picker, "先切到此程式，再執行下面的動作")
        )
        evade.add_row(_setting_row("同時執行", self.action))
        self.target_app = DropDown()
        self.target_app.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
        self.target_app.activated.connect(self._target_app_chosen)
        self.refresh_apps = IconButton("refresh", "重新整理", size=theme.D["control_height"])
        self.refresh_apps.clicked.connect(self.reload_apps)
        picker = QWidget()
        picker_row = QHBoxLayout(picker)
        picker_row.setContentsMargins(0, 0, 0, 0)
        picker_row.setSpacing(theme.D["space_8"])
        picker_row.addWidget(self.target_app)
        picker_row.addWidget(self.refresh_apps)
        target_block = QWidget()
        target_layout = QVBoxLayout(target_block)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(theme.D["space_8"])
        self.target_chips = QWidget()
        self.target_chip_layout = FlowLayout(self.target_chips)
        self.target_app_chips: list[AppChip] = []
        target_layout.addWidget(self.target_chips)
        target_layout.addWidget(picker)
        self.target_app_row = evade.add_row(
            _setting_row("目標程式", target_block, dependent=True, stacked=True)
        )
        self.reload_apps()
        self.restore_after_alert = Switch()
        self.restore_after_alert.setChecked(s.restore_after_alert)
        self.restore_after_alert.toggled.connect(
            lambda value: self._update(restore_after_alert=bool(value))
        )
        self.restore_after_alert_row = evade.add_row(
            _setting_row("警示結束後還原視窗", self.restore_after_alert, dependent=True)
        )
        self._sync_target_rows()
        add_section("閃避", evade)

        away = _SettingsCard()
        self.away_action = _combo(_AWAY_ACTIONS, s.away_action)
        self.away_action.currentIndexChanged.connect(self._away_action_changed)
        away.add_row(_setting_row("離座後", self.away_action))
        self.away_minutes = Stepper()
        self.away_minutes.setRange(1, 60)
        self.away_minutes.setSuffix(" 分")
        self.away_minutes.setValue(s.away_minutes)
        self.away_minutes.valueChanged.connect(lambda value: self._update(away_minutes=int(value)))
        self.away_minutes_row = away.add_row(
            _setting_row("等待時間", self.away_minutes, dependent=True)
        )
        self._set_dependency(self.away_minutes_row, s.away_action != "none")
        add_section("離座時", away)
        self._sync_target_rows()

        known = _SettingsCard()
        self.unknown_level = _combo(_UNKNOWN_LEVELS, s.unknown_level)
        self.unknown_level.currentIndexChanged.connect(
            lambda _: self._update(unknown_level=str(self.unknown_level.currentData()))
        )
        known.add_row(_setting_row("沒標記過的人", self.unknown_level))
        self.people_editor: PeopleEditor | None = None
        if self._people is not None:
            self.people_editor = PeopleEditor(self._people, self._people_changed)
            people_wrap = QWidget()
            people_wrap.setObjectName("peopleEditorContainer")
            people_layout = QVBoxLayout(people_wrap)
            people_layout.setContentsMargins(
                theme.D["row_h_padding"],
                theme.D["row_v_padding"],
                theme.D["row_h_padding"],
                theme.D["row_v_padding"],
            )
            people_layout.addWidget(self.people_editor)
            known.add_row(people_wrap)
        add_section("認得的人", known)

        system = _SettingsCard()
        if self._autostart is not None:
            get_autostart, _ = self._autostart
            self.autostart = Switch()
            self.autostart.setChecked(get_autostart())
            self.autostart.toggled.connect(self._toggle_autostart)
            system.add_row(_setting_row("開機自動啟動", self.autostart))
        self.hotkey = QKeySequenceEdit(QKeySequence(s.mirror_hotkey))
        self.hotkey.setObjectName("hotkeyEdit")
        self.hotkey.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
        self.hotkey.setMaximumSequenceLength(1)
        self.hotkey.setClearButtonEnabled(False)
        self.hotkey.editingFinished.connect(self._hotkey_edited)
        self.hotkey.setAccessibleName("小鏡子快捷鍵")
        self.hotkey_hint = _hint("")
        self.hotkey_hint.hide()
        hotkey_block = QWidget()
        hotkey_layout = QVBoxLayout(hotkey_block)
        hotkey_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_layout.setSpacing(theme.D["space_4"])
        hotkey_layout.addWidget(self.hotkey)
        hotkey_layout.addWidget(self.hotkey_hint)
        self.small_mirror_row = system.add_row(
            _setting_row("小鏡子快捷鍵", hotkey_block, "低調模式開啟中")
        )
        self.small_mirror_notice = _required_hint(self.small_mirror_row)
        self.discreet_disabled_rows = (
            (self.show_mirror_row, self.show_mirror_notice),
            (self.edge_flash_row, self.edge_flash_notice),
            (self.sound_row, self.sound_notice),
            (self.small_mirror_row, self.small_mirror_notice),
        )
        self.hide_hotkey = QKeySequenceEdit(QKeySequence(s.hide_hotkey))
        self.hide_hotkey.setObjectName("hotkeyEdit")
        self.hide_hotkey.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
        self.hide_hotkey.setMaximumSequenceLength(1)
        self.hide_hotkey.setClearButtonEnabled(True)
        self.hide_hotkey.editingFinished.connect(self._hide_hotkey_edited)
        self.hide_hotkey.setAccessibleName("隱藏視窗快捷鍵")
        self.hide_hotkey_hint = _hint("按一下縮小目標程式，再按一下還原")
        hide_hotkey_block = QWidget()
        hide_hotkey_layout = QVBoxLayout(hide_hotkey_block)
        hide_hotkey_layout.setContentsMargins(0, 0, 0, 0)
        hide_hotkey_layout.setSpacing(theme.D["space_4"])
        hide_hotkey_layout.addWidget(self.hide_hotkey)
        hide_hotkey_layout.addWidget(self.hide_hotkey_hint)
        system.add_row(_setting_row("隱藏視窗快捷鍵", hide_hotkey_block))
        self.hide_hotkey_style = _combo(_HIDE_HOTKEY_STYLES, s.hide_hotkey_style)
        self.hide_hotkey_style.currentIndexChanged.connect(self._hide_hotkey_style_changed)
        system.add_row(_setting_row("快捷鍵動作", self.hide_hotkey_style))
        self.mark_false_hotkey = QKeySequenceEdit(QKeySequence(s.mark_false_hotkey))
        self.mark_real_hotkey = QKeySequenceEdit(QKeySequence(s.mark_real_hotkey))
        self._mark_hotkey_controls = {
            "false_alarm": (self.mark_false_hotkey, "mark_false_hotkey", "剛才的警示是誤報"),
            "real": (self.mark_real_hotkey, "mark_real_hotkey", "剛才的警示是真的"),
        }
        self._mark_hotkey_hints: dict[str, QLabel] = {}
        for kind, (mark_editor, _field, description) in self._mark_hotkey_controls.items():
            mark_editor.setObjectName("hotkeyEdit")
            mark_editor.setFixedSize(theme.D["dropdown_width"], theme.D["control_height"])
            mark_editor.setMaximumSequenceLength(1)
            mark_editor.setClearButtonEnabled(True)
            mark_editor.editingFinished.connect(lambda kind=kind: self._mark_hotkey_edited(kind))
            hint = _hint(description)
            self._mark_hotkey_hints[kind] = hint
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(theme.D["space_4"])
            block_layout.addWidget(mark_editor)
            block_layout.addWidget(hint)
            title = "誤報標記快捷鍵" if kind == "false_alarm" else "真實標記快捷鍵"
            mark_editor.setAccessibleName(title)
            system.add_row(_setting_row(title, block))
        for shortcut_editor in (
            self.hotkey,
            self.hide_hotkey,
            self.mark_false_hotkey,
            self.mark_real_hotkey,
        ):
            line_edit = shortcut_editor.findChild(QLineEdit)
            if line_edit is not None:
                line_edit.setPlaceholderText("按下快捷鍵")
        self._set_hide_hotkey_hint()
        self._sync_target_rows()
        self.pause_when_locked = Switch()
        self.pause_when_locked.setChecked(s.pause_when_locked)
        self.pause_when_locked.toggled.connect(
            lambda value: self._update(pause_when_locked=bool(value))
        )
        system.add_row(_setting_row("電腦鎖定時暫停", self.pause_when_locked))
        self.diagnostics = Switch()
        self.diagnostics.setChecked(s.diagnostics)
        self.diagnostics.toggled.connect(self._diagnostics_changed)
        system.add_row(_setting_row("診斷記錄", self.diagnostics, "只記錄框與判定，不存影像"))
        self.open_diagnostics = QPushButton("開啟記錄資料夾")
        self.open_diagnostics.setObjectName("textButton")
        self.open_diagnostics.clicked.connect(
            lambda: self.on_open_diagnostics() if self.on_open_diagnostics else None
        )
        self.open_diagnostics_row = system.add_row(
            _setting_row("", self.open_diagnostics, dependent=True)
        )
        self._set_dependency(self.open_diagnostics_row, s.diagnostics)
        self.update_check = Switch()
        self.update_check.setChecked(s.update_check_enabled)
        self.update_check.setAccessibleName("自動檢查更新")
        self.update_check_row = system.add_row(
            _setting_row("自動檢查更新", self.update_check, "每天最多查一次 GitHub，只送出版本號")
        )
        self.update_status_label = _hint("")
        self.skip_update_button = QPushButton("略過")
        self.skip_update_button.setObjectName("textButton")
        self.skip_update_button.setStyleSheet(f"font-size: {theme.FONT_SIZES['caption']}px;")
        self.skip_update_button.clicked.connect(
            lambda: self.on_skip_update() if self.on_skip_update else None
        )
        self.check_updates_button = QPushButton("立即檢查")
        self.check_updates_button.setObjectName("textButton")
        self.check_updates_button.clicked.connect(self._check_updates_clicked)
        self.update_check.toggled.connect(self._update_check_changed)
        self.check_updates_row = QWidget()
        self.check_updates_row.setObjectName("settingRow")
        status_layout = QHBoxLayout(self.check_updates_row)
        status_layout.setContentsMargins(
            theme.D["row_h_padding"],
            theme.D["row_v_padding"],
            theme.D["row_h_padding"],
            theme.D["row_v_padding"],
        )
        status_layout.setSpacing(theme.D["space_8"])
        status_layout.addWidget(self.update_status_label, 1)
        status_layout.addWidget(self.skip_update_button)
        status_layout.addWidget(self.check_updates_button)
        system.add_row(self.check_updates_row)
        self.refresh_update_status()
        add_section("系統", system)

        schedule = _SettingsCard()
        self.schedule_enabled = Switch()
        self.schedule_enabled.setChecked(s.schedule_enabled)
        self.schedule_enabled.toggled.connect(self._schedule_enabled_changed)
        schedule.add_row(_setting_row("啟用監看時段", self.schedule_enabled))
        self.schedule_start = QTimeEdit(QTime.fromString(s.schedule_start, "HH:mm"))
        self.schedule_end = QTimeEdit(QTime.fromString(s.schedule_end, "HH:mm"))
        for editor, name in (
            (self.schedule_start, "開始時間"),
            (self.schedule_end, "結束時間"),
        ):
            editor.setDisplayFormat("HH:mm")
            editor.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
            editor.setFixedSize(theme.D["schedule_time_width"], theme.D["control_height"])
            editor.setAccessibleName(name)
            editor.setStyleSheet(
                f"QTimeEdit {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER};"
                f" border-radius: {theme.RADIUS['control']}px; padding: 0 8px; }}"
                "QTimeEdit:focus[keyboardFocus='true'] {"
                f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
            )
            editor.timeChanged.connect(self._schedule_time_changed)
        time_fields = QWidget()
        time_layout = QHBoxLayout(time_fields)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(theme.D["space_8"])
        time_layout.addWidget(self.schedule_start)
        time_layout.addWidget(QLabel("至"))
        time_layout.addWidget(self.schedule_end)
        self.schedule_fields_row = schedule.add_row(
            _setting_row("時間", time_fields, dependent=True, stacked=True)
        )
        weekday_box = QWidget()
        weekday_layout = QHBoxLayout(weekday_box)
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_layout.setSpacing(theme.D["weekday_gap"])
        self.schedule_day_buttons: list[QPushButton] = []
        for day, label in enumerate(_WEEKDAYS):
            button = QPushButton(label)
            button.setObjectName("weekdayChip")
            button.setCheckable(True)
            button.setChecked(day in s.schedule_days)
            button.setFixedSize(theme.D["weekday_size"], theme.D["weekday_size"])
            button.setAccessibleName(f"星期{label}")
            button.setStyleSheet(
                f"QPushButton {{ min-height: 0; padding: 0; border: 1px solid {theme.BORDER};"
                f" background: {theme.RAISED}; border-radius: {theme.RADIUS['chip']}px;"
                f" color: {theme.TEXT_2}; }}"
                f"QPushButton:checked {{ background: {theme.SELECTION}; border-color:"
                f" {theme.ACCENT}; color: {theme.TEXT}; }}"
                "QPushButton:focus[keyboardFocus='true'] {"
                f" border: {theme.D['focus_width']}px solid {theme.ACCENT}; }}"
            )
            button.clicked.connect(self._schedule_days_changed)
            weekday_layout.addWidget(button)
            self.schedule_day_buttons.append(button)
        self.schedule_days_row = schedule.add_row(
            _setting_row("星期", weekday_box, dependent=True, stacked=True)
        )
        self._set_dependency(self.schedule_fields_row, s.schedule_enabled)
        self._set_dependency(self.schedule_days_row, s.schedule_enabled)
        add_section("監看時段", schedule)

        form.addStretch(1)
        footer = QWidget()
        footer.setObjectName("privacyFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, theme.D["space_24"], 0, 0)
        footer_layout.setSpacing(theme.D["space_8"])
        lock = QLabel()
        lock.setPixmap(
            theme.glyph_icon("lock", theme.TEXT_3, theme.D["space_14"]).pixmap(
                theme.D["space_14"], theme.D["space_14"]
            )
        )
        lock.setFixedSize(theme.D["space_14"], theme.D["space_14"])
        footer_layout.addWidget(lock, 0, Qt.AlignmentFlag.AlignTop)
        footer_copy = QVBoxLayout()
        footer_copy.setContentsMargins(0, 0, 0, 0)
        footer_copy.setSpacing(theme.D["space_4"])
        self.privacy_label = _hint("影像只在記憶體中處理，不錄影、不存檔。")
        footer_copy.addWidget(self.privacy_label)
        self.version_label = _hint(f"版本 {self._display_version}")
        footer_copy.addWidget(self.version_label)
        footer_layout.addLayout(footer_copy, 1)
        form.addWidget(footer)
        scroll.setWidget(content)
        self._wheel_guard = SettingsWheelGuard(content, scroll)
        QWidget.setTabOrder(content, self.sensitivity.buttons[0])
        wrapper = QVBoxLayout(panel)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        self._drawer_header = QWidget()
        self._drawer_header.setFixedHeight(theme.D["drawer_header_height"])
        header = QHBoxLayout(self._drawer_header)
        header.setContentsMargins(theme.D["space_20"], 0, theme.D["space_20"], 0)
        heading = QLabel("設定")
        heading.setObjectName("windowTitle")
        header.addWidget(heading)
        header.addStretch(1)
        self.drawer_close_button = IconButton("close", "關閉設定", theme.D["drawer_close_size"])
        self.drawer_close_button.clicked.connect(self.close_drawer)
        header.addWidget(self.drawer_close_button)
        wrapper.addWidget(self._drawer_header)
        self._drawer_header.hide()
        wrapper.addWidget(scroll)
        return panel

    # ---------------------------------------------------------- responsive UI
    @property
    def drawer_open(self) -> bool:
        return self._drawer_open

    def _toggle_drawer(self) -> None:
        self.close_drawer() if self._drawer_open else self.open_drawer()

    def open_drawer(self) -> None:
        if not self._narrow or self._drawer_open:
            return
        self._drawer_open = True
        self._animate_drawer(opening=True)
        self._settings_content.setFocus(Qt.FocusReason.OtherFocusReason)

    def close_drawer(self) -> None:
        if not self._drawer_open:
            return
        self._drawer_open = False
        self._animate_drawer(opening=False)

    def _animate_drawer(self, *, opening: bool) -> None:
        if self._drawer_animation is not None:
            self._drawer_animation.stop()
            self._drawer_animation.deleteLater()
            self._drawer_animation = None
        width = theme.D["settings_width"]
        shown_x = max(0, self._root.width() - width)
        hidden_x = self._root.width()
        start_x = self._panel.x() if self._panel.isVisible() else hidden_x
        self._panel.setGeometry(start_x, 0, width, self._root.height())
        self._scrim.setGeometry(self._root.rect())
        if not self._scrim.isVisible():
            self._scrim_effect.setOpacity(0.0)
        self._scrim.show()
        self._panel.show()
        self._scrim.raise_()
        self._panel.raise_()
        if not motion.animations_enabled():
            self._panel.move(shown_x if opening else hidden_x, 0)
            self._scrim_effect.setOpacity(1.0 if opening else 0.0)
            if not opening:
                self._panel.hide()
                self._scrim.hide()
            return
        panel_animation = QPropertyAnimation(self._panel, b"pos", self)
        panel_animation.setDuration(theme.D["drawer_duration"])
        panel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        panel_animation.setStartValue(self._panel.pos())
        panel_animation.setEndValue(QPoint(shown_x if opening else hidden_x, 0))
        scrim_animation = QPropertyAnimation(self._scrim_effect, b"opacity", self)
        scrim_animation.setDuration(theme.D["drawer_duration"])
        scrim_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        scrim_animation.setStartValue(self._scrim_effect.opacity())
        scrim_animation.setEndValue(1.0 if opening else 0.0)
        group = QParallelAnimationGroup(self)
        group.addAnimation(panel_animation)
        group.addAnimation(scrim_animation)
        group.finished.connect(lambda: self._drawer_animation_finished(group, opening))
        self._drawer_animation = group
        group.start()

    def _drawer_animation_finished(self, group: QParallelAnimationGroup, opening: bool) -> None:
        if self._drawer_animation is group:
            self._drawer_animation = None
        group.deleteLater()
        if not opening and not self._drawer_open:
            self._panel.hide()
            self._scrim.hide()

    def _sync_responsive_layout(self, *, force: bool = False) -> None:
        narrow = self.width() < theme.D["drawer_threshold"]
        if not force and narrow == self._narrow:
            if narrow:
                self._scrim.setGeometry(self._root.rect())
                width = theme.D["settings_width"]
                target_x = (
                    max(0, self._root.width() - width) if self._drawer_open else self._root.width()
                )
                self._panel.resize(width, self._root.height())
                if self._drawer_animation is None:
                    self._panel.move(target_x, 0)
                else:
                    for animation in self._drawer_animation.findChildren(QPropertyAnimation):
                        if animation.targetObject() is self._panel:
                            animation.setEndValue(QPoint(target_x, 0))
            return
        self._narrow = narrow
        self.settings_button.setVisible(narrow)
        self._drawer_header.setVisible(narrow)
        if self._drawer_animation is not None:
            self._drawer_animation.stop()
            self._drawer_animation.deleteLater()
            self._drawer_animation = None
        if narrow:
            self._outer.removeWidget(self._panel)
            self._panel.setParent(self._root)
            self._scrim.setGeometry(self._root.rect())
            self._panel.setGeometry(
                self._root.width(), 0, theme.D["settings_width"], self._root.height()
            )
            self._panel.hide()
            self._scrim.hide()
            self._drawer_open = False
        else:
            self._drawer_open = False
            self._scrim.hide()
            self._panel.hide()
            self._panel.setParent(self._root)
            if self._outer.indexOf(self._panel) < 0:
                self._outer.addWidget(self._panel)
            self._panel.show()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "_panel"):
            self._sync_responsive_layout()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._settings_content.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape and self._drawer_open:
            self.close_drawer()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- state
    @property
    def settings(self) -> Settings:
        return self._settings

    def _update(self, **changes: object) -> None:
        preview = any(
            getattr(self._settings, key) != value
            for key, value in changes.items()
            if key
            in ("discreet_cue", "discreet_corner", "discreet_screen_mode", "discreet_screens")
        )
        self._settings = replace(self._settings, **changes).normalized()  # type: ignore[arg-type]
        self._sync_discreet_ui()
        if hasattr(self, "update_status_label"):
            self.refresh_update_status()
        self._on_settings(self._settings)
        if preview and self._settings.discreet and self.on_preview_cue is not None:
            self.on_preview_cue(False)

    @staticmethod
    def _default_camera_lister() -> list[object]:
        from desk_sentry.camera_shared import list_cameras

        return list(list_cameras())

    def _request_camera_list(self, *, show_loading: bool = True) -> None:
        self._camera_retry_timer.stop()
        self._camera_list_generation += 1
        generation = self._camera_list_generation
        if show_loading:
            self.camera.blockSignals(True)
            self.camera.clear()
            self.camera.addItem("讀取中…", None)
            self.camera.setEnabled(False)
            self.camera.blockSignals(False)

        def load_cameras() -> None:
            try:
                cameras = list(self._list_cameras())
            except Exception:
                cameras = []
            with suppress(RuntimeError):
                self._camera_list_ready.emit(generation, cameras)

        Thread(target=load_cameras, name="camera-list", daemon=True).start()

    def _apply_camera_list(self, generation: int, cameras: object) -> None:
        if generation != self._camera_list_generation:
            return
        entries: list[tuple[int, str, str]] = []
        if isinstance(cameras, list):
            for position, camera in enumerate(cameras):
                name = str(getattr(camera, "name", "") or "").strip()
                if not name:
                    continue
                try:
                    index = int(getattr(camera, "index", position))
                except (TypeError, ValueError):
                    index = position
                camera_id = str(getattr(camera, "id", "") or "")
                entries.append((index, camera_id, name))

        stored_id = self._settings.camera_id
        stored_name = self._settings.camera_name
        self.camera.blockSignals(True)
        self.camera.clear()
        if not entries:
            if stored_id or stored_name:
                offline_name = stored_name or "選擇的攝影機"
                self.camera.addItem(
                    f"{offline_name}（未連接）",
                    (self._settings.camera_index, stored_id, stored_name),
                )
            else:
                self.camera.addItem("找不到攝影機（點開重新搜尋）", None)
            self.camera.setEnabled(True)
            self.camera.blockSignals(False)
            self._camera_retry_timer.start()
            return

        name_counts: dict[str, int] = {}
        for _index, _camera_id, name in entries:
            name_counts[name] = name_counts.get(name, 0) + 1

        matched_entry = -1
        if stored_id:
            stored_id_key = stored_id.casefold()
            matched_entry = next(
                (
                    i
                    for i, (_index, camera_id, _name) in enumerate(entries)
                    if camera_id.casefold() == stored_id_key
                ),
                -1,
            )
        if matched_entry < 0 and stored_name:
            matches = [
                (i, abs(index - self._settings.camera_index))
                for i, (index, _camera_id, name) in enumerate(entries)
                if name == stored_name
            ]
            if matches:
                matched_entry = min(matches, key=lambda match: (match[1], match[0]))[0]
        if matched_entry < 0 and not stored_id and not stored_name:
            matched_entry = next(
                (
                    i
                    for i, (index, _camera_id, _name) in enumerate(entries)
                    if index == self._settings.camera_index
                ),
                -1,
            )

        offline = bool(stored_id or stored_name) and matched_entry < 0
        if offline:
            offline_name = stored_name or "選擇的攝影機"
            self.camera.addItem(
                f"{offline_name}（未連接）",
                (self._settings.camera_index, stored_id, stored_name),
            )

        occurrences: dict[str, int] = {}
        for index, camera_id, name in entries:
            occurrences[name] = occurrences.get(name, 0) + 1
            ordinal = occurrences[name]
            label = f"{name} ({ordinal})" if name_counts[name] > 1 and ordinal > 1 else name
            self.camera.addItem(label, (index, camera_id, name))

        selected = 0 if offline else matched_entry
        auto_select = not stored_id and not stored_name and selected < 0
        if auto_select:
            selected = 0
        elif not offline:
            selected = max(0, selected)
        self.camera.setCurrentIndex(selected)
        self.camera.setEnabled(True)
        self.camera.blockSignals(False)

        if auto_select:
            camera_index, camera_id, camera_name = self.camera.itemData(selected)
            self._update(
                camera_index=int(camera_index),
                camera_id=str(camera_id),
                camera_name=str(camera_name),
            )

    def _camera_chosen(self, index: int) -> None:
        data = self.camera.itemData(index)
        if not isinstance(data, tuple) or len(data) != 3:
            return
        camera_index, camera_id, camera_name = data
        selected = (int(camera_index), str(camera_id), str(camera_name))
        current = (
            self._settings.camera_index,
            self._settings.camera_id,
            self._settings.camera_name,
        )
        if selected == current:
            return
        self._update(
            camera_index=selected[0],
            camera_id=selected[1],
            camera_name=selected[2],
        )

    def set_camera_status(
        self,
        backend: str,
        *,
        backend_detail: str = "",
        subtype: str = "",
        width: int = 0,
        height: int = 0,
        fps: float = 0.0,
        last_error: str | None = None,
        meeting_active: bool = False,
        unresponsive: bool = False,
        acknowledged: bool = True,
        release_failed: bool = False,
    ) -> None:
        del backend_detail, subtype, width, height, fps
        release_requested = (
            self._pause_reason in ("meeting", "manual_yield")
            or backend == "yielded"
            or (backend == "exclusive" and meeting_active and self._settings.auto_yield_camera)
        )
        transient_release_failed = release_requested and not acknowledged and unresponsive
        disconnected = last_error == "CameraDisconnectedError" and (
            backend == "pending" or unresponsive
        )
        if release_failed:
            text = "無法確認已讓出攝影機；若會議軟體拿不到畫面，請結束 Desk Sentry"
            color = theme.STATUS_COLOR[Status.ERROR]
        elif transient_release_failed:
            text = "無法讓出攝影機，稍後自動重試"
            color = theme.STATUS_COLOR[Status.ERROR]
        elif disconnected:
            text = "選擇的攝影機未連接"
            color = theme.STATUS_COLOR[Status.ERROR]
        elif release_requested and not acknowledged:
            text = "正在讓出攝影機…"
            color = theme.STATUS_COLOR[Status.PAUSED]
        elif release_requested:
            text = "已讓出給視訊會議，結束後自動恢復"
            color = theme.STATUS_COLOR[Status.WATCHING]
        elif backend == "locked":
            text = "已暫停 · 電腦鎖定中"
            color = theme.STATUS_COLOR[Status.PAUSED]
        elif backend == "schedule":
            text = "已暫停 · 非監看時段"
            color = theme.STATUS_COLOR[Status.PAUSED]
        elif not self._settings.armed or backend in ("paused", "manual"):
            text = "已暫停"
            color = theme.STATUS_COLOR[Status.PAUSED]
        elif unresponsive:
            text = "攝影機沒有回應，稍後自動重試"
            color = theme.STATUS_COLOR[Status.ERROR]
        elif backend == "shared":
            text = "共用中，可與視訊會議同時使用"
            color = theme.STATUS_COLOR[Status.CLEAR]
        elif backend == "exclusive":
            text = (
                "相容模式：Teams、Zoom 等會議軟體開麥克風時自動讓出"
                if self._settings.auto_yield_camera
                else "相容模式：開會時需先暫停 Desk Sentry"
            )
            color = theme.STATUS_COLOR[Status.ALERT]
        else:
            text = "連線中…"
            color = theme.STATUS_COLOR[Status.PAUSED]
        presentation = (text, color)
        if presentation == self._camera_status_presentation:
            return
        self._camera_status_presentation = presentation
        self.camera_status.setText(text)
        self.camera_status.setAccessibleName(f"攝影機目前狀態：{text}")
        self.camera_status_dot.setStyleSheet(
            f"background: {color}; border-radius: {theme.D['dot'] // 2}px;"
        )

    def set_camera_diagnostics(self, text: str) -> None:
        self._camera_diagnostics = str(text)
        self.copy_camera_diagnostics.setEnabled(bool(self._camera_diagnostics))

    def _copy_camera_diagnostics(self) -> None:
        QApplication.clipboard().setText(self._camera_diagnostics)
        self.copy_camera_diagnostics.setText("已複製")
        QTimer.singleShot(2000, self._reset_camera_copy_button)

    def _reset_camera_copy_button(self) -> None:
        self.copy_camera_diagnostics.setText("複製診斷資訊")

    def _screen_clicked(self, index: int) -> None:
        entries = self.screen_catalog.entries()
        if not 0 <= index < len(entries):
            return
        settings = self._settings
        selected, _fallback = self.screen_catalog.selected(
            settings.discreet_screen_mode, settings.discreet_screens
        )
        references = list(
            settings.discreet_screens
            if settings.discreet_screen_mode == "selected"
            else (e.reference for e in selected)
        )
        target = entries[index].reference
        matching = [r for r in references if same_screen(r, target)]
        if matching:
            other_connected = [e for e in selected if not same_screen(e.reference, target)]
            if not other_connected:
                self.screen_map.reject(index)
                if settings.discreet_screen_mode != "selected":
                    self._update(
                        discreet_screen_mode="selected", discreet_screens=tuple(references)
                    )
                return
            references = [r for r in references if not same_screen(r, target)]
        else:
            references.append(target)
        self._update(discreet_screen_mode="selected", discreet_screens=tuple(references))

    def _remove_screen(self, reference: dict[str, str]) -> None:
        self._update(
            discreet_screens=tuple(r for r in self._settings.discreet_screens if r != reference)
        )

    def _refresh_screen_ui(self) -> None:
        if not hasattr(self, "screen_map"):
            return
        s = self._settings
        entries = self.screen_catalog.entries()
        selected, _fallback = self.screen_catalog.selected(
            s.discreet_screen_mode, s.discreet_screens
        )
        self.screen_map.configure(
            s.discreet_screen_mode, s.discreet_screens, s.discreet_cue, s.discreet_corner
        )
        self.screen_selection_hint.setText(f"點選切換 · 已選 {len(selected)}／{len(entries)}")
        while self.offline_screen_layout.count():
            item = self.offline_screen_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        missing = [
            r for r in s.discreet_screens if not any(same_screen(r, e.reference) for e in entries)
        ]
        if s.discreet_screen_mode == "selected":
            for ref in missing:
                chip = AppChip(f"螢幕 {ref.get('number', '?')}（未連接）")
                chip.removed.connect(lambda _text, reference=ref: self._remove_screen(reference))
                self.offline_screen_layout.addWidget(chip)
        self.offline_screens.setVisible(s.discreet_screen_mode == "selected" and bool(missing))

    def _discreet_changed(self, enabled: bool) -> None:
        self._update(discreet=bool(enabled))

    def _discreet_cue_changed(self, _index: int) -> None:
        self._update(discreet_cue=str(self.discreet_cue.currentData()))

    def _sync_discreet_ui(self) -> None:
        discreet = self._settings.discreet
        self.discreet.blockSignals(True)
        self.discreet.setChecked(discreet)
        self.discreet.blockSignals(False)
        for control, value in (
            (self.discreet_cue, self._settings.discreet_cue),
            (self.discreet_corner, self._settings.discreet_corner),
            (self.discreet_screen_mode, self._settings.discreet_screen_mode),
        ):
            control.blockSignals(True)
            control.setCurrentIndex(max(0, control.findData(value)))
            control.blockSignals(False)

        self._set_dependency(self.discreet_cue_row, discreet)
        dot_selected = self._settings.discreet_cue == "dot"
        self.discreet_corner_row.setVisible(dot_selected)
        self._set_dependency(self.discreet_corner_row, discreet and dot_selected)
        screens_visible = self._settings.discreet_cue != "none"
        self.discreet_screen_row.setVisible(screens_visible)
        self.discreet_screen_detail.setVisible(screens_visible)
        self._set_dependency(self.discreet_screen_row, discreet)
        self._set_dependency(self.discreet_screen_detail, discreet)
        self._refresh_screen_ui()
        for row, notice in self.discreet_disabled_rows:
            self._set_dependency(row, not discreet)
            notice.setVisible(discreet)
        self._set_dependency(self.corner_row, not discreet and self.show_mirror.isChecked())
        self.compact_button.setEnabled(not discreet)
        self.compact_button.setToolTip("低調模式開啟中" if discreet else "小鏡子模式")
        self.mirror.set_discreet(discreet)
        if discreet:
            self._has_frame = False
            self._info_row.hide()

        icon = (
            theme.app_icon() if discreet else theme.app_icon(theme.STATUS_COLOR[self._last_status])
        )
        self._app_icon.setPixmap(icon.pixmap(theme.D["toolbar_icon"], theme.D["toolbar_icon"]))
        self.setWindowIcon(icon)
        self.setWindowTitle("Desk Sentry 後照鏡")
        self._toolbar_title.setVisible(True)

    def _sensitivity_changed(self, _index: int) -> None:
        value = str(self.sensitivity.currentData())
        self._sensitivity_hint.setText(_SENSITIVITY_HINT[value])
        self._update(sensitivity=value)

    def _show_mirror_changed(self, enabled: bool) -> None:
        self._set_dependency(self.corner_row, enabled and not self._settings.discreet)
        self._update(show_mirror=bool(enabled))

    def _diagnostics_changed(self, enabled: bool) -> None:
        self._set_dependency(self.open_diagnostics_row, enabled)
        self._stats.setVisible(enabled)
        self._update(diagnostics=bool(enabled))

    @staticmethod
    def _set_dependency(row: QWidget, enabled: bool) -> None:
        row.setEnabled(enabled)
        effect = row.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(row)
            row.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if enabled else theme.DEPENDENT_DISABLED_OPACITY)

    def _action_changed(self, _index: int) -> None:
        action = str(self.action.currentData())
        self._sync_target_rows()
        if action == "minimize_app":
            self.reload_apps()
        self._update(action=action)

    def _away_action_changed(self, _index: int) -> None:
        action = str(self.away_action.currentData())
        self._set_dependency(self.away_minutes_row, action != "none")
        self._update(away_action=action)
        self._sync_target_rows()

    def _hide_hotkey_style_changed(self, _index: int) -> None:
        style = str(self.hide_hotkey_style.currentData())
        self._update(hide_hotkey_style=style)
        self._set_hide_hotkey_hint()
        self._sync_target_rows()

    def _schedule_enabled_changed(self, enabled: bool) -> None:
        self._set_dependency(self.schedule_fields_row, enabled)
        self._set_dependency(self.schedule_days_row, enabled)
        self._update(schedule_enabled=bool(enabled))

    def _schedule_time_changed(self, _time: QTime) -> None:
        self._update(
            schedule_start=self.schedule_start.time().toString("HH:mm"),
            schedule_end=self.schedule_end.time().toString("HH:mm"),
        )

    def _schedule_days_changed(self) -> None:
        days = tuple(
            day for day, button in enumerate(self.schedule_day_buttons) if button.isChecked()
        )
        self._update(schedule_days=days)

    def reload_apps(self) -> None:
        targets = self._settings.target_apps
        self._render_target_apps()
        self.target_app.blockSignals(True)
        self.target_app.clear()
        self.target_app.addItem("＋ 加入程式…", "")
        seen: set[str] = set()
        apps = self._list_apps()
        for exe, title in sorted(apps, key=lambda item: item[0]):
            exe = exe.strip().lower()
            if not exe or exe in seen or exe in targets:
                continue
            seen.add(exe)
            short = title if len(title) <= 28 else title[:27] + "…"
            self.target_app.addItem(f"{exe}　{short}", exe)
        self.target_app.setCurrentIndex(0)
        full = len(targets) >= MAX_TARGET_APPS
        self.target_app.setEnabled(not full)
        self.target_app.setToolTip("最多 8 個程式" if full else "")
        self.target_app.blockSignals(False)
        self._reload_cover_apps(apps)

    def _reload_cover_apps(self, apps: list[tuple[str, str]]) -> None:
        self.cover_app.blockSignals(True)
        self.cover_app.clear()
        self.cover_app.addItem("不使用", "")
        seen: set[str] = set()
        for exe, title in sorted(apps, key=lambda item: item[0]):
            exe = exe.strip().lower()
            if not exe or exe in seen:
                continue
            seen.add(exe)
            self.cover_app.addItem(exe, exe)
            self.cover_app.setItemData(
                self.cover_app.count() - 1, title, Qt.ItemDataRole.ToolTipRole
            )
        current = self._settings.cover_app
        if current and current not in seen:
            self.cover_app.addItem(current, current)
        self.cover_app.setCurrentIndex(max(0, self.cover_app.findData(current)))
        self._sync_cover_tooltip()
        self.cover_app.blockSignals(False)

    def _sync_cover_tooltip(self, _index: int = 0) -> None:
        self.cover_app.setToolTip(
            str(self.cover_app.currentData(Qt.ItemDataRole.ToolTipRole) or "")
        )

    def _cover_app_chosen(self, _index: int) -> None:
        self._update(cover_app=str(self.cover_app.currentData() or ""))
        self._sync_target_rows()

    def _target_app_chosen(self, _index: int) -> None:
        exe = str(self.target_app.currentData() or "")
        if exe and exe not in self._settings.target_apps:
            self._update(target_apps=(*self._settings.target_apps, exe))
        self.reload_apps()

    def _render_target_apps(self) -> None:
        while self.target_chip_layout.count():
            item = self.target_chip_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self.target_app_chips = []
        for exe in self._settings.target_apps:
            chip = AppChip(exe)
            chip.removed.connect(self._remove_target_app)
            self.target_chip_layout.addWidget(chip)
            self.target_app_chips.append(chip)
        self.target_chips.setVisible(bool(self.target_app_chips))

    def _remove_target_app(self, exe: str) -> None:
        self._update(target_apps=tuple(item for item in self._settings.target_apps if item != exe))
        self.reload_apps()

    def _sync_target_rows(self) -> None:
        minimize_action = str(self.action.currentData()) == "minimize_app"
        away_minimize = (
            hasattr(self, "away_action") and str(self.away_action.currentData()) == "minimize_app"
        )
        hide_minimize = bool(self._settings.hide_hotkey) and (
            not hasattr(self, "hide_hotkey_style")
            or str(self.hide_hotkey_style.currentData()) == "minimize"
        )
        self.target_app_row.setVisible(minimize_action or away_minimize or hide_minimize)
        self.restore_after_alert_row.setVisible(minimize_action or bool(self._settings.cover_app))

    def _set_hide_hotkey_hint(self) -> None:
        style = (
            str(self.hide_hotkey_style.currentData())
            if hasattr(self, "hide_hotkey_style")
            else self._settings.hide_hotkey_style
        )
        self.hide_hotkey_hint.setText(
            "按一下蓋上黑幕，再按一下移開"
            if style == "curtain"
            else "按一下縮小目標程式，再按一下還原"
        )

    def _hotkey_edited(self) -> None:
        text = self.hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if conflicts(text, self._settings.hide_hotkey):
            self.hotkey.setKeySequence(QKeySequence(self._settings.mirror_hotkey))
            self.hide_hotkey_hint.setText("與小鏡子快捷鍵相同")
            return
        if any(
            conflicts(text, chord)
            for chord in (self._settings.mark_false_hotkey, self._settings.mark_real_hotkey)
        ):
            self.hotkey.setKeySequence(QKeySequence(self._settings.mirror_hotkey))
            self.hotkey_hint.setText("與標記快捷鍵相同")
            self.hotkey_hint.show()
            return
        ok = bool(text) and (self.on_hotkey is None or self.on_hotkey(text))
        if ok:
            self._update(mirror_hotkey=text)
            self.hotkey_hint.clear()
            self.hotkey_hint.hide()
            self._set_hide_hotkey_hint()
        else:
            self.hotkey.setKeySequence(QKeySequence(self._settings.mirror_hotkey))
            self.hotkey_hint.setText(
                "這個組合被其他程式佔用或不支援，請換一個（需含 Ctrl / Alt / Shift / Win）。"
            )
            self.hotkey_hint.show()

    def _hide_hotkey_edited(self) -> None:
        text = self.hide_hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if conflicts(text, self._settings.mirror_hotkey):
            self.hide_hotkey.setKeySequence(QKeySequence(self._settings.hide_hotkey))
            self.hide_hotkey_hint.setText("與小鏡子快捷鍵相同")
            return
        if any(
            conflicts(text, chord)
            for chord in (self._settings.mark_false_hotkey, self._settings.mark_real_hotkey)
        ):
            self.hide_hotkey.setKeySequence(QKeySequence(self._settings.hide_hotkey))
            self.hide_hotkey_hint.setText("與標記快捷鍵相同")
            return
        ok = self.on_hide_hotkey is None or self.on_hide_hotkey(text)
        if ok:
            self._update(hide_hotkey=text)
            self._set_hide_hotkey_hint()
            self._sync_target_rows()
        else:
            self.hide_hotkey.setKeySequence(QKeySequence(self._settings.hide_hotkey))
            self.hide_hotkey_hint.setText(
                "這個組合被其他程式佔用或不支援，請換一個（需含 Ctrl / Alt / Shift / Win）。"
            )

    def _mark_hotkey_edited(self, kind: str) -> None:
        editor, field_name, description = self._mark_hotkey_controls[kind]
        text = editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        others = [
            self._settings.mirror_hotkey,
            self._settings.hide_hotkey,
            self._settings.mark_real_hotkey
            if kind == "false_alarm"
            else self._settings.mark_false_hotkey,
        ]
        invalid = bool(text) and parse_hotkey(text) is None
        duplicate = any(conflicts(text, other) for other in others)
        hint = self._mark_hotkey_hints[kind]
        if (
            invalid
            or duplicate
            or (self.on_mark_hotkey is not None and not self.on_mark_hotkey(kind, text))
        ):
            editor.setKeySequence(QKeySequence(getattr(self._settings, field_name)))
            hint.setText("與其他快捷鍵相同" if duplicate else "快捷鍵無法使用，請換一個組合")
            return
        self._update(**{field_name: text})
        hint.setText(description)

    def set_mark_hotkey_unavailable(self, kind: str) -> None:
        self._mark_hotkey_hints[kind].setText("快捷鍵無法使用")

    def _people_changed(self) -> None:
        if self.on_people_changed is not None:
            self.on_people_changed()

    def refresh_people(self) -> None:
        if self.people_editor is not None:
            self.people_editor.refresh()

    def _toggle_autostart(self, enabled: bool) -> None:
        assert self._autostart is not None
        get_autostart, set_autostart = self._autostart
        set_autostart(enabled)
        actual = get_autostart()
        if actual != enabled:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(actual)
            self.autostart.blockSignals(False)

    def _pick_self(self, x: float, y: float) -> None:
        boxes = [Box(*track.box) for track in self._last_tracks]
        zone = zone_for_point(boxes, x, y)
        if zone is not None:
            self._update(self_zone=zone, exclude_self=True)
            self.exclude_self.setChecked(True)

    def update_settings(self, **changes: object) -> None:
        self._update(**changes)

    def _update_check_changed(self, enabled: bool) -> None:
        self._update(update_check_enabled=enabled)

    def _check_updates_clicked(self) -> None:
        if self._shown_update_status() == "available" and not self._update_checking:
            if self.on_open_update:
                self.on_open_update()
        elif self.on_check_updates:
            self.on_check_updates()

    def set_update_checking(self, checking: bool) -> None:
        self._update_checking = checking
        self.refresh_update_status()

    def _shown_update_status(self) -> str:
        status = self._settings.update_last_status
        # Sharing an installation/config with a release must not make a dev
        # build offer a saved automatic result. Manual dev results have their
        # own persisted status; keep the original release record intact.
        if "+" in self._display_version and status in ("available", "latest", "ignored"):
            return "never"
        return status

    def refresh_update_status(self, *, now: datetime | None = None) -> None:
        settings = self._settings
        status, version = self._shown_update_status(), settings.update_latest_version
        text = "尚未檢查"
        stamp = ""
        if settings.update_last_checked > 0:
            try:
                checked = datetime.fromtimestamp(settings.update_last_checked)
                today = (now or datetime.now()).date()
                stamp = checked.strftime(
                    "今天 %H:%M 檢查" if checked.date() == today else "%m/%d %H:%M 檢查"
                )
            except (ValueError, OverflowError, OSError):
                pass
        if self._update_checking:
            text = "檢查中…"
        elif status == "latest":
            text = "已是最新版" + (f" · {stamp}" if stamp else "")
        elif status == "available":
            text = f"有新版 {version}"
        elif status == "ignored":
            text = f"已略過 {version}" + (f" · {stamp}" if stamp else "")
        elif status == "error":
            text = "無法連線 GitHub，稍後再試"
        elif status == "development":
            text = (
                # Full dev version is already in the footer; keep this to one line.
                f"開發版 · 最新正式版 {version}" if version else "開發版"
            )
        self.update_status_label.setText(text)
        available = status == "available" and not self._update_checking
        self.update_status_label.setStyleSheet(f"color: {theme.ACCENT};" if available else "")
        self.skip_update_button.setVisible(available)
        self.check_updates_button.setText("前往下載" if available else "立即檢查")
        self.check_updates_button.setEnabled(not self._update_checking)
        self.check_updates_button.setStyleSheet(
            f"color: {theme.TEXT_3};" if self._update_checking else ""
        )

    def set_self_zone(self, zone: tuple[float, float, float, float]) -> None:
        self._settings = replace(self._settings, self_zone=zone)

    def set_unseen(self, count: int) -> None:
        self.history_button.set_badge(count)

    def set_pause_reason(
        self,
        reason: str | None,
        *,
        now: datetime | None = None,
        next_start: datetime | None = None,
        acknowledged: bool = True,
    ) -> None:
        self._pause_reason = reason
        pause_copy = {
            "locked": "已暫停 · 電腦鎖定中",
            "schedule": "已暫停 · 非監看時段",
        }
        copy = pause_copy.get(reason) if reason is not None else None
        if reason in ("meeting", "manual_yield"):
            if not acknowledged:
                copy = "正在讓出攝影機"
            elif reason == "meeting":
                copy = "已暫停 · 會議中（已讓出攝影機）"
            else:
                copy = "已暫停 · 已讓出攝影機"
        self.pause_reason.setText(copy or "")
        self.pause_reason.setVisible(copy is not None)

        automatic_detail: str | None = None
        if reason == "locked":
            automatic_detail = "電腦鎖定中"
        elif reason == "schedule":
            current = now or datetime.now()
            boundary = next_start or next_boundary(
                current,
                self._settings.schedule_start,
                self._settings.schedule_end,
                self._settings.schedule_days,
            )
            automatic_detail = "非監看時段"
            if boundary is not None:
                start_copy = boundary.strftime("%H:%M")
                if boundary.date() != current.date():
                    start_copy = f"週{_WEEKDAYS[boundary.weekday()]} {start_copy}"
                automatic_detail = f"非監看時段 · {start_copy} 自動開始"
        elif reason in ("meeting", "manual_yield"):
            automatic_detail = copy
        self.mirror.set_automatic_pause_detail(automatic_detail)
        self._render_armed()

    def _resume_if_manual(self) -> None:
        if self._pause_reason not in _AUTOMATIC_PAUSE_REASONS:
            self.toggle_armed()

    def toggle_armed(self) -> None:
        self._update(armed=not self._settings.armed)
        self._render_armed()

    def set_armed(self, armed: bool) -> None:
        if armed != self._settings.armed:
            self.toggle_armed()

    def _render_armed(self) -> None:
        self.arm_button.set_armed(self._settings.armed)
        self.arm_button.set_automatic_pause(
            self._settings.armed and self._pause_reason in _AUTOMATIC_PAUSE_REASONS
        )

    def show_snapshot(self, snap: Snapshot) -> None:
        if self._settings.discreet:
            snap = replace(
                snap,
                frame=None,
                tracks=(),
                self_zone=None,
                alert_thumbnail=None,
                alert_face=None,
            )
            self._last_tracks = ()
        if snap.frame is not None:
            self._last_tracks = snap.tracks
        self._render_status(snap)
        if self.isVisible() and not self.isMinimized():
            self.mirror.set_snapshot(snap)

    def _render_status(self, snap: Snapshot) -> None:
        self._last_status = snap.status
        if self._settings.discreet or snap.status in (
            Status.PAUSED,
            Status.NO_CAMERA,
            Status.ERROR,
        ):
            self._has_frame = False
        elif snap.frame is not None:
            self._has_frame = True
        self._info_row.setVisible(self._has_frame)
        title, detail = theme.STATUS_TEXT[snap.status]
        if snap.status in (Status.ALERT, Status.APPROACHING) and snap.intruders > 1:
            detail = f"{snap.intruders} 人在你身後移動"
        self._status_title.setText(title)
        self._status_detail.setText(detail)
        icon = (
            theme.app_icon()
            if self._settings.discreet
            else theme.app_icon(theme.STATUS_COLOR[snap.status])
        )
        self._app_icon.setPixmap(icon.pixmap(theme.D["toolbar_icon"], theme.D["toolbar_icon"]))
        self.setWindowIcon(icon)
        self.arm_button.set_status(snap.status)
        self._people_stats.setText(
            f"畫面中其他人 {snap.people}" if snap.people else "畫面中沒有其他人"
        )
        if snap.status in (Status.PAUSED, Status.NO_CAMERA, Status.ERROR):
            self._stats.setText("—")
        else:
            self._stats.setText(f"{snap.camera_fps:0.1f} fps · {snap.latency_ms:0.0f} ms")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        self.hide()
        if self.on_hide_to_tray is not None:
            self.on_hide_to_tray()
