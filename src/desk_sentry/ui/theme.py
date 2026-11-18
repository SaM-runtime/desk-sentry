"""Colours, status copy and the global stylesheet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from desk_sentry.sentry import Role, Status

BG = "#0E1116"
SURFACE = "#161B22"
RAISED = "#1E252E"
BORDER = "#29313C"
TEXT = "#E6EAF0"
MUTED = "#8B95A3"
ACCENT = "#3DD6B5"
NEUTRAL_ICON_FILL = "#6B7480"
NEUTRAL_ICON_BORDER = "#8B95A3"
NOTICE = "#B8A16A"

STATUS_COLOR = {
    Status.NOTICE: NOTICE,
    Status.PAUSED: "#6B7480",
    Status.NO_CAMERA: "#F2545B",
    Status.ERROR: "#F2545B",
    Status.CLEAR: "#3DD68C",
    Status.AWAY: "#A78BFA",
    Status.WATCHING: "#7AA2F7",
    Status.ALERT: "#F5A524",
    Status.APPROACHING: "#F2545B",
}
STATUS_TEXT = {
    Status.NOTICE: ("預警", "留意後方動態"),
    Status.PAUSED: ("已暫停", "攝影機已釋放，不會偵測"),
    Status.NO_CAMERA: ("找不到攝影機", "會自動重試；確認攝影機連接或是否被占用"),
    Status.ERROR: ("偵測暫時出錯", "2 秒後自動重試"),
    Status.CLEAR: ("後方淨空", "監看中"),
    Status.AWAY: ("你不在座位", "沒有人需要提醒；回到座位後自動恢復"),
    Status.WATCHING: ("有人，但不需提醒", "遠處經過、靜止或確認中的人不會觸發警示"),
    Status.ALERT: ("後方有人", "有人在你身後移動"),
    Status.APPROACHING: ("有人正在接近", "身後的人正朝你走來"),
}
ROLE_COLOR = {
    Role.SELF: ACCENT,
    Role.INTRUDER: STATUS_COLOR[Status.ALERT],
    Role.APPROACHING: STATUS_COLOR[Status.APPROACHING],
    Role.STILL: STATUS_COLOR[Status.WATCHING],
    Role.PASSING: STATUS_COLOR[Status.WATCHING],
    Role.KNOWN: STATUS_COLOR[Status.WATCHING],
    Role.FAR: "#6B7480",
    Role.PENDING: "#6B7480",
}
ROLE_TEXT = {
    Role.SELF: "你",
    Role.INTRUDER: "移動中",
    Role.APPROACHING: "接近中",
    Role.STILL: "靜止",
    Role.PASSING: "遠處經過",
    Role.KNOWN: "認得",
    Role.FAR: "太遠",
    Role.PENDING: "確認中",
}

# Design tokens: DESIGN-SPEC-03 sections 1-7. Geometry for painter glyphs is
# normalized to an 18px icon canvas; all physical sizing lives here.
RAISED_HOVER = "#242C37"
RAISED_PRESSED = "#1A2028"
SUNKEN = "#11161C"
STAGE = "#0A0D11"
BORDER_STRONG = "#3A4452"
DIVIDER = "#222A33"
TEXT_2 = MUTED
TEXT_3 = "#5E6875"
ON_ACCENT = "#06201A"
ON_STATUS = "#0B0E12"
ACCENT_HOVER = "#5BE0C4"
WHITE = "#FFFFFF"
SWITCH_OFF = "#C9D1DC"
SELECTION = "#26443F"
SCROLL_HANDLE = "#333C48"
OVERLAY_CSS = "rgba(11,14,18,0.72)"
OVERLAY = QColor(11, 14, 18, 184)
SCRIM = "rgba(0,0,0,0.40)"
SCRIM_COLOR = QColor(0, 0, 0, 102)
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Microsoft JhengHei UI"'
MONO_FAMILY = '"Cascadia Mono", Consolas'
FONT_SIZES = {"caption": 11, "small": 12, "body": 13, "pill": 14, "title": 15, "status": 20}
SPACING = (4, 8, 12, 16, 20, 24, 32)
RADIUS = {"chip": 6, "control": 8, "card": 12, "stage": 16, "window": 16}
D: dict[str, int] = {
    "main_width": 1180,
    "main_height": 640,
    "min_width": 640,
    "min_height": 420,
    "breakpoint": 1000,
    "panel_width": 340,
    "toolbar_height": 40,
    "icon_button": 36,
    "icon": 18,
    "app_icon": 16,
    "control_height": 32,
    "switch_width": 36,
    "switch_height": 20,
    "switch_knob": 16,
    "switch_inset": 2,
    "stepper_width": 112,
    "stepper_button": 28,
    "stepper_value_width": 56,
    "combo_width": 132,
    "row_height": 52,
    "arm_height": 36,
    "arm_padding": 14,
    "badge_min": 16,
    "badge_inset": 2,
    "dot": 8,
    "small_dot": 6,
    "focus_width": 2,
    "focus_inset": 2,
    "mini_width": 384,
    "mini_height": 216,
    "history_width": 760,
    "popup_width": 360,
    "popup_shadow": 16,
}
MOTION = {
    "switch": 150,
    "segment": 180,
    "color": 200,
    "drawer": 200,
    "enter": 180,
    "fade_in": 150,
    "fade_out": 200,
    "hover": 120,
    "alert": 1600,
    "approaching": 1000,
    "track_tau": 120,
}
GLYPH_STROKE = 1.5
DEPENDENT_DISABLED_OPACITY = 0.4
D.update(
    {
        "divider_height": 1,
        "dependent_marker_height": 2,
        "main_default_width": 1180,
        "main_default_height": 640,
        "main_min_width": 640,
        "main_min_height": 420,
        "settings_width": 340,
        "main_h_padding": 24,
        "main_v_padding": 20,
        "toolbar_icon": 16,
        "toolbar_gap": 8,
        "section_gap": 24,
        "section_first_top": 4,
        "row_min_height": 52,
        "row_v_padding": 12,
        "row_h_padding": 14,
        "drawer_threshold": 1000,
        "drawer_duration": 200,
        "stage_radius": 16,
        "stage_status_height": 32,
        "stats_height": 20,
        "dropdown_width": 132,
        **{f"space_{n}": n for n in (4, 6, 8, 12, 14, 16, 20, 24)},
    }
)
SPACE_4, SPACE_6, SPACE_8, SPACE_12 = 4, 6, 8, 12
SPACING_4, SPACING_8, SPACING_12, SPACING_16, SPACING_20 = 4, 8, 12, 16, 20
RADIUS_STAGE, RADIUS_CHIP = 16, 6
CARD_RADIUS, WINDOW_RADIUS, CHIP_RADIUS = 12, 16, 6
FONT_SMALL_PX, FONT_BODY_PX, FONT_PILL_PX, FONT_STATUS_PX = 12, 13, 14, 20
WINDOW_TITLE_SIZE, SMALL_SIZE, PILL_SIZE = 15, 12, 14
STATUS_PILL_HEIGHT, COMPACT_PILL_HEIGHT = 32, 24

STYLESHEET = f"""
* {{ font-family: {FONT_FAMILY}; font-size: 13px; color: {TEXT}; }}
QMainWindow, #root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
#panel {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
#title {{ font-size: 15px; font-weight: 600; }}
#sectionTitle {{ color: {TEXT_3}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
#settingsCard {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 12px; }}
#statusTitle {{ font-size: 20px; font-weight: 600; }}
#statusDetail, #stats {{ color: {TEXT_2}; }}
QComboBox, QSpinBox, QLineEdit, QKeySequenceEdit {{
 background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px;
 padding: 0px 8px; min-height: 30px; selection-background-color: {SELECTION};
}}
QKeySequenceEdit QLineEdit {{ background: {RAISED_HOVER}; border: none; border-radius: 6px;
 font-family: {MONO_FAMILY}; font-size: 12px; }}
QComboBox:focus[keyboardFocus="true"], QSpinBox:focus[keyboardFocus="true"],
QLineEdit:focus[keyboardFocus="true"], QKeySequenceEdit:focus[keyboardFocus="true"] {{
 border: 2px solid {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {BORDER};
 border-radius: 8px; selection-background-color: {SELECTION}; }}
QPushButton {{ background: {RAISED_HOVER}; border: none; border-radius: 8px;
 padding: 0px 12px; min-height: 32px; font-weight: 500; }}
QPushButton:hover {{ background: {RAISED_HOVER}; }}
QPushButton:pressed {{ background: {RAISED_PRESSED}; }}
QPushButton:focus[keyboardFocus="true"] {{ border: 2px solid {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ON_ACCENT}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#textButton {{ color: {ACCENT}; background: transparent; padding: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: transparent; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HANDLE}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
 background: none; height: 0;
}}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


def app_icon(color: str = ACCENT) -> QIcon:
    """A rounded rear-view-mirror glyph with a status dot."""

    icon = QIcon()
    image = Path(__file__).resolve().parents[1] / "assets" / "desk_sentry.png"
    for size in (16, 24, 32, 48, 64, 256):
        pixmap = QPixmap(str(image)).scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(size * 0.605, size * 0.365, size * 0.13, size * 0.13))
        p.end()
        icon.addPixmap(pixmap)
    return icon


def neutral_icon() -> QIcon:
    """Neutral rounded-square identity without camera or mirror imagery."""

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = max(1.0, size * 0.16)
        radius = size * 0.2
        body = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        painter.setPen(QPen(QColor(NEUTRAL_ICON_BORDER), max(1.0, size / 24)))
        painter.setBrush(QColor(NEUTRAL_ICON_FILL))
        painter.drawRoundedRect(body, radius, radius)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def glyph_icon(name: str, color: str = TEXT_2, size: int = D["icon"]) -> QIcon:
    """Consistent path glyphs drawn in an 18-unit coordinate space."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 18, size / 18)
    pen = QPen(QColor(color), GLYPH_STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    if name in ("clock", "history"):
        path.addEllipse(QRectF(2, 2, 14, 14))
        path.moveTo(9, 5)
        path.lineTo(9, 9)
        path.lineTo(12, 11)
    elif name in ("pip", "compact"):
        path.addRoundedRect(QRectF(1.5, 3, 15, 12), 2, 2)
        path.addRoundedRect(QRectF(9, 8, 5, 4), 0.5, 0.5)
    elif name in ("settings", "gear"):
        import math

        for i in range(32):
            angle = math.pi * 2 * i / 32
            r = 7.5 if i % 4 in (0, 1) else 5.7
            x, y = 9 + r * math.cos(angle), 9 + r * math.sin(angle)
            if i:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
        path.closeSubpath()
        path.addEllipse(QRectF(6.5, 6.5, 5, 5))
    elif name in ("close", "x"):
        path.moveTo(5, 5)
        path.lineTo(13, 13)
        path.moveTo(13, 5)
        path.lineTo(5, 13)
    elif name == "refresh":
        path.arcMoveTo(QRectF(3, 3, 12, 12), 40)
        path.arcTo(QRectF(3, 3, 12, 12), 40, 300)
        path.moveTo(15, 2)
        path.lineTo(15, 6)
        path.lineTo(11, 6)
    elif name == "lock":
        path.addRoundedRect(QRectF(4, 8, 10, 8), 2, 2)
        path.moveTo(6, 8)
        path.lineTo(6, 5)
        path.cubicTo(6, 1, 12, 1, 12, 5)
        path.lineTo(12, 8)
    elif name == "chevron":
        path.moveTo(4, 7)
        path.lineTo(9, 12)
        path.lineTo(14, 7)
    else:
        path.addRoundedRect(QRectF(1, 4, 16, 9), 3, 3)
        path.moveTo(9, 13)
        path.lineTo(9, 17)
    p.drawPath(path)
    p.end()
    return QIcon(pixmap)


def dot_pixmap(color: str, size: int = D["dot"]) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.end()
    return pixmap


# Mirror drawing geometry and motion settling thresholds.
MIRROR_MIN_SIZE = (240, 135)
STAGE_BORDER_INSET = 1
NO_IMAGE_ICON = 40
NO_IMAGE_TITLE_HEIGHT = 24
NO_IMAGE_DETAIL_HEIGHT = 18
PRIMARY_BUTTON_HEIGHT = 36
PRIMARY_BUTTON_MIN_WIDTH = 112
PRIMARY_BUTTON_H_PADDING = 14
TRACK_SMOOTH_DURATION = 720
TRACK_SNAP_EPSILON = 0.0005
SELF_BRACKET_WIDTH = 2.0
ROLE_BRACKET_WIDTH = 1.5
BRACKET_MAX_ARM = 18
BRACKET_ARM_RATIO = 0.20
ALERT_BOX_WIDTH = 2.5
ALERT_GLOW_INNER_WIDTH = 6
ALERT_GLOW_OUTER_WIDTH = 10
ALERT_GLOW_INNER_ALPHA = 0.25
ALERT_GLOW_OUTER_ALPHA = 0.12
CHIP_HEIGHT = 20
TRACK_CHIP_GAP = 4
TRACK_CHIP_H_PADDING = 8
TRACK_CHIP_DOT_GAP = 6
STATUS_PILL_INSET = 12
STATUS_PILL_H_PADDING = 12
STATUS_PILL_DOT_GAP = 8
COMPACT_PILL_INSET = 8
COMPACT_PILL_H_PADDING = 8
STAGE_ALERT_BORDER_WIDTH = 2
STAGE_BORDER_WIDTH = 1
STAGE_GLOW_WIDTH = 12
STAGE_GLOW_ALPHA_MIN = 0.15
STAGE_GLOW_ALPHA_MAX = 0.45
SELF_ZONE_ALPHA = 0.40
SELF_ZONE_DASH = (4, 4)
UNSEEN_HEIGHT = 20
UNSEEN_INSET = 8
D.update(
    {
        "mini_resize_edge": 7,
        "mini_min_width": 240,
        "mini_min_height": 135,
        "mini_screen_margin": 16,
        "mini_radius": 14,
        "mini_view_radius": 10,
        "mini_hover_height": 36,
        "mini_hover_button": 28,
        "mini_hover_top": 4,
        "mini_hover_right": 8,
        "mini_hover_gap": 4,
        "popup_card_padding": 10,
        "popup_title_height": 28,
        "popup_content_gap": 8,
        "popup_mirror_min_width": 200,
        "popup_mirror_min_height": 112,
        "popup_shadow_y": 12,
        "popup_shadow_blur": 32,
        "history_columns": 4,
        "history_gap": 12,
        "history_padding": 20,
        "history_thumb_radius": 10,
        "history_empty_icon": 40,
        "history_unseen_dot": 8,
        "dependent_marker_width": 10,
    }
)

ROLE_BOX_ALPHA = 0.80
MIRROR_ASPECT_WIDTH = 16
MIRROR_ASPECT_HEIGHT = 9
STYLESHEET += f"""
#windowTitle {{ font-size: 15px; font-weight: 600; }}
#settingTitle {{ font-size: 13px; font-weight: 500; }}
#settingHint, #peopleStats {{ font-size: 12px; color: {TEXT_2}; }}
#statsHint, #privacyFooter QLabel {{ font-size: 12px; color: {TEXT_3}; }}
#stats {{ font-family: {MONO_FAMILY}; font-size: 11px; color: {TEXT_3}; }}
#rowDivider {{ background: {DIVIDER}; border: none; margin-left: 14px; margin-right: 14px; }}
#dependentMarker {{ background: {BORDER_STRONG}; border: none; }}
#drawerScrim {{ background: {SCRIM}; }}
#privacyFooter {{ background: transparent; }}
"""

MINI_HOVER_TOP = QColor(0, 0, 0, 140)
POPUP_SHADOW = QColor(0, 0, 0, 115)

MINI_BORDER = QColor(255, 255, 255, 20)

D["dropdown_chevron"] = 10

CHIP_BOUNDARY_MARGIN = 4
D.update({"drawer_header_height": 48, "drawer_close_size": 28})

# Multi-program picker geometry. It reuses the existing raised-hover chip palette.
D.update(
    {
        "target_chip_height": 24,
        "target_chip_h_padding": 8,
        "target_chip_remove_size": 18,
        "target_chip_gap": 4,
        "target_chip_flow_gap": 6,
        "target_chip_max_width": 240,
        "schedule_time_width": 96,
        "weekday_size": 30,
        "weekday_gap": 4,
    }
)
