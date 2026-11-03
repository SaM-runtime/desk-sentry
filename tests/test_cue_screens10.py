"""Screen targeting uses injected monitors, never the user's physical displays."""

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal

from desk_sentry.config import Settings, load, save
from desk_sentry.sentry import Status
from desk_sentry.ui.discreet import DiscreetCue, ScreenCatalog, screen_rectangles, screen_reference
from desk_sentry.ui.main_window import MainWindow
from test_discreet_cue06 import _Timer


class Screen(QObject):
    geometryChanged = Signal(object)
    availableGeometryChanged = Signal(object)

    def __init__(self, number=1, rect=None, serial=None):
        super().__init__()
        self.number = number
        self.rect = rect or QRect((number - 1) * 1920, 0, 1920, 1080)
        self.available = self.rect.adjusted(0, 0, 0, -40)
        self.serial = str(number) if serial is None else serial
        self.display = f"DISPLAY{number}"

    def geometry(self):
        return QRect(self.rect)

    def availableGeometry(self):
        return QRect(self.available)

    def serialNumber(self):
        return self.serial

    def model(self):
        return "U2720Q" if self.serial else ""

    def manufacturer(self):
        return "DELL" if self.serial else ""

    def name(self):
        return self.display


def test_new_settings_use_all_screens_top_right_but_preserve_old_corner(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}")
    settings = load(path)
    assert settings.discreet_corner == "top-right"
    assert settings.discreet_screen_mode == "all"
    assert settings.discreet_screens == ()
    path.write_text(json.dumps({"discreet_corner": "bottom-right"}))
    assert load(path).discreet_corner == "bottom-right"


def test_all_screens_have_cues_inside_available_geometry(qapp):
    screens = [Screen(i) for i in range(1, 5)]
    cue = DiscreetCue(screens_provider=lambda: screens, screen_provider=lambda: screens[0])
    try:
        cue.update_status(Status.ALERT)
        assert len(cue.windows) == 4
        assert all(
            s.availableGeometry().contains(w.geometry())
            for s, w in zip(screens, cue.windows, strict=True)
        )
    finally:
        cue.close()


@pytest.fixture
def monitors():
    return [
        Screen(1, QRect(0, 0, 2560, 1440)),
        Screen(2, QRect(2560, -240, 2560, 1440)),
        Screen(3, QRect(-1920, 360, 1920, 1080)),
        Screen(4, QRect(5120, 100, 1080, 1920)),
    ]


@pytest.mark.parametrize(
    "mode,numbers", [("all", [1, 2, 3, 4]), ("primary", [1]), ("selected", [2, 4])]
)
def test_selected_screen_count_and_serial_survives_display_renumber(qapp, monitors, mode, numbers):
    catalog = ScreenCatalog(lambda: monitors, lambda: monitors[0])
    references = tuple(screen_reference(monitors[i], i + 1) for i in (1, 3))
    monitors[1].display, monitors[3].display = monitors[3].display, monitors[1].display
    cue = DiscreetCue(catalog=catalog)
    try:
        cue.configure("dot", "top-right", mode, references)
        cue.update_status(Status.ALERT)
        assert len(cue.windows) == len(numbers)
        assert [w.geometry().topRight() for w in cue.windows] == [
            monitors[n - 1].available.topRight() + QPoint(-2, 2) for n in numbers
        ]
    finally:
        cue.close()


def test_empty_hardware_identity_uses_name_and_geometry(qapp):
    screens = [Screen(1, serial=""), Screen(2, serial="")]
    catalog = ScreenCatalog(lambda: screens, lambda: screens[0])
    selected, fallback = catalog.selected("selected", (screen_reference(screens[1]),))
    assert not fallback and [e.screen for e in selected] == [screens[1]]
    saved = screen_reference(screens[1])
    screens[1].display = "other"
    selected, fallback = catalog.selected("selected", (saved,))
    assert fallback and selected[0].screen is screens[0]


@pytest.mark.parametrize("corner", ["top-left", "top-right", "bottom-left", "bottom-right"])
def test_dot_stays_outside_every_taskbar_edge(qapp, corner):
    screen = Screen()
    screen.available = screen.rect.adjusted(48, 30, -20, -60)
    cue = DiscreetCue(screen_provider=lambda: screen, corner=corner)
    try:
        cue.update_status(Status.ALERT)
        assert screen.available.contains(cue.window.geometry())
        assert cue.window.width() == cue.window.height() == 6
        cue.configure("line", corner)
        assert cue.window.geometry() == QRect(
            screen.available.x(), screen.available.y(), screen.available.width(), 2
        )
    finally:
        cue.close()


def test_hotplug_while_visible_and_fallback_logs_once(qapp, monitors):
    current = monitors[:]
    logs = []
    catalog = ScreenCatalog(lambda: current, lambda: current[0] if current else None)
    cue = DiscreetCue(catalog=catalog, on_fallback=lambda: logs.append("fallback"))
    try:
        cue.configure("dot", "top-right", "selected", (screen_reference(monitors[3]),))
        cue.update_status(Status.ALERT)
        removed_window = cue.window
        current.remove(monitors[3])
        catalog.refresh()
        assert len(cue.windows) == 1 and cue.window is not removed_window
        assert logs == ["fallback"]
        cue.update_status(Status.ALERT)
        catalog.refresh()
        assert logs == ["fallback"]
        current.append(monitors[3])
        catalog.refresh()
        assert cue.window.geometry().right() > 5000
        cue.configure("dot", "top-right", "all")
        assert len(cue.windows) == 4
        current.pop()
        catalog.refresh()
        assert len(cue.windows) == 3
        monitors[0].available = monitors[0].available.adjusted(0, 40, 0, 0)
        monitors[0].availableGeometryChanged.emit(monitors[0].available)
        assert cue.windows[0].y() == 42
        cue.hide()
        assert not cue.visible and all(not w.isVisible() for w in cue.windows)
    finally:
        cue.close()


def test_multiscreen_deadlines_and_feedback_share_one_clock(qapp, monitors):
    now, timer, feedback = [0.0], _Timer(), _Timer()
    cue = DiscreetCue(
        screens_provider=lambda: monitors,
        screen_provider=lambda: monitors[0],
        clock=lambda: now[0],
        timer=timer,
        feedback_timer=feedback,
    )
    try:
        cue.update_status(Status.ALERT)
        cue.update_status(Status.CLEAR)
        assert timer.started[-1] == 2000
        now[0] = 1.8
        cue.pulse_feedback()
        assert feedback.started[-1] == 400
        assert len({w.color for w in cue.windows}) == 1
        now[0] = 2.0
        timer.fire()
        assert all(w.isVisible() for w in cue.windows)
        now[0] = 2.2
        feedback.fire()
        assert not cue.visible and all(not w.isVisible() for w in cue.windows)
    finally:
        cue.close()


def test_layout_is_uniform_scale_with_four_pixel_gap(qapp, monitors):
    from PySide6.QtCore import QRectF

    entries = ScreenCatalog(lambda: monitors, lambda: monitors[0]).entries()
    rectangles = screen_rectangles(entries, QRectF(0, 0, 320, 96))
    scales = [r.width() / e.bounds.width() for e, r in zip(entries, rectangles, strict=True)]
    assert max(scales) - min(scales) < 1e-9
    for i, rect in enumerate(rectangles):
        assert rect.width() / rect.height() == pytest.approx(
            entries[i].bounds.width() / entries[i].bounds.height()
        )
        for other in rectangles[i + 1 :]:
            assert not rect.intersects(other)
            assert (
                max(rect.left(), other.left()) - min(rect.right(), other.right()) >= 4 - 1e-6
                or max(rect.top(), other.top()) - min(rect.bottom(), other.bottom()) >= 4 - 1e-6
            )


def test_map_click_modes_last_selection_and_offline_chip(qtbot, monitors):
    catalog = ScreenCatalog(lambda: monitors, lambda: monitors[0])
    window = MainWindow(Settings(discreet=True), lambda _s: None, screen_catalog=catalog)
    qtbot.addWidget(window)
    window.show()

    def click(index):
        qtbot.mouseClick(
            window.screen_map,
            Qt.MouseButton.LeftButton,
            pos=window.screen_map.rectangles()[index].center().toPoint(),
        )

    click(1)
    assert window.settings.discreet_screen_mode == "selected"
    assert len(window.settings.discreet_screens) == 3
    window._update(discreet_screen_mode="primary")
    click(3)
    assert {r["serial"] for r in window.settings.discreet_screens} == {"1", "4"}
    click(0)
    assert {r["serial"] for r in window.settings.discreet_screens} == {"4"}
    click(3)
    assert len(window.settings.discreet_screens) == 1
    monitors.pop()
    catalog.refresh()
    assert "已選 1／3" in window.screen_selection_hint.text().replace("\u2060", "")
    assert not window.offline_screens.isHidden()
    assert window.offline_screen_layout.count() == 1
    window._update(discreet_cue="none")
    assert window.discreet_screen_row.isHidden() and window.discreet_screen_detail.isHidden()


def test_settings_screen_references_roundtrip_and_future_schema_preserved(tmp_path):
    from desk_sentry.config import CURRENT_SCHEMA_VERSION

    reference = screen_reference(Screen(2), 2)
    settings = Settings(discreet_screen_mode="selected", discreet_screens=(reference,))
    path = tmp_path / "settings.json"
    save(settings, path)
    assert load(path).discreet_screens == (reference,)
    path.write_text(json.dumps({"schema_version": CURRENT_SCHEMA_VERSION + 1, "future": 9}))
    original = path.read_bytes()
    save(replace(load(path), discreet_screen_mode="primary"), path)
    assert path.read_bytes() == original


def test_preview_is_neutral_no_fallback_log_and_has_shared_expiry(qtbot, monitors):
    logs = []
    catalog = ScreenCatalog(lambda: monitors, lambda: monitors[0])
    cue = DiscreetCue(catalog=catalog, on_fallback=lambda: logs.append("fallback"))
    try:
        cue.configure("line", "top-right", "selected", ({"serial": "missing"},))
        cue.preview(3000, identify=True)
        assert len(cue.identifiers) == 4 and len(cue._preview_windows) == 1
        assert cue._preview_timer.interval() == 3000
        assert cue._preview_windows[0].color == "#6B7480"
        assert logs == [] and not cue.visible
        # Deliver the actual timer timeout without waiting three wall-clock seconds.
        cue._preview_timer.timeout.emit()
        assert cue.identifiers == [] and cue._preview_windows == []
        cue.preview()
        assert cue._preview_timer.interval() == 1500
        cue._preview_timer.timeout.emit()
        assert cue._preview_windows == []
    finally:
        cue.close()
