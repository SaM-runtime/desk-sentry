"""Presentation contracts for the Task03 specification; no detector dependencies."""

import weakref
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from desk_sentry.camera_shared import CameraInfo
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.ui import motion, theme
from desk_sentry.ui.main_window import MainWindow
from desk_sentry.ui.mirror_view import MirrorView


def _frame(status: Status, *, tracks: bool = True) -> Snapshot:
    return Snapshot(
        status,
        frame=np.zeros((90, 160, 3), dtype=np.uint8),
        tracks=(TrackView(1, (0.1, 0.2, 0.3, 0.8), Role.INTRUDER, 0.9),) if tracks else (),
    )


def _running(widget: MainWindow | MirrorView) -> list[QAbstractAnimation]:
    return [
        item
        for item in widget.findChildren(QAbstractAnimation)
        if item.state() == QAbstractAnimation.State.Running
    ]


def test_responsive_threshold_and_drawer_dismissal(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    window = MainWindow(Settings(), lambda _: None)
    qtbot.addWidget(window)
    window.show()
    window.resize(1000, 540)
    QApplication.processEvents()
    assert window._panel.isVisible()
    assert not window.settings_button.isVisible()
    window.resize(999, 540)
    QApplication.processEvents()
    assert not window._panel.isVisible()
    assert window.settings_button.isVisible()
    window.settings_button.click()
    assert window.drawer_open and window._panel.isVisible()
    assert window._panel.width() == 340
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert not window.drawer_open and not window._panel.isVisible()
    window.resize(700, 480)
    window.open_drawer()
    qtbot.mouseClick(window._scrim, Qt.MouseButton.LeftButton, pos=QPoint(20, 80))
    assert not window.drawer_open
    window.resize(1000, 540)
    QApplication.processEvents()
    assert window._panel.isVisible() and not window._scrim.isVisible()


def test_reduced_motion_controls_drawer_and_alert_leave_no_animation(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    window = MainWindow(Settings(), lambda _: None)
    qtbot.addWidget(window)
    window.resize(820, 540)
    window.show()
    window.show_snapshot(_frame(Status.APPROACHING))
    window.open_drawer()
    window.sound.click()
    window.sensitivity.buttons[2].click()
    assert not _running(window)
    assert not any(timer.isActive() for timer in window.findChildren(QTimer))
    assert not window.mirror.animations_running
    window.close_drawer()
    assert not _running(window)


def test_clear_empty_mirror_stops_after_exit_and_color_settle(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    view = MirrorView()
    qtbot.addWidget(view)
    view.show()
    view.set_snapshot(_frame(Status.ALERT))
    assert view.animations_running
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    qtbot.waitUntil(lambda: not view.animations_running, timeout=1500)
    assert not _running(view)
    assert not any(timer.isActive() for timer in view.findChildren(QTimer))


def test_hidden_mirror_and_live_motion_preference_stop_animation(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    view = MirrorView()
    qtbot.addWidget(view)
    view.show()
    view.set_snapshot(_frame(Status.ALERT))
    assert view.animations_running
    view.hide()
    assert not view.animations_running
    view.show()
    view.set_snapshot(_frame(Status.ALERT))
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    view.set_snapshot(_frame(Status.APPROACHING))
    assert not view.animations_running


def test_segment_switch_and_stepper_report_settings_values(qtbot: QtBot) -> None:
    reports: list[Settings] = []
    window = MainWindow(
        Settings(),
        reports.append,
        list_cameras=lambda: [CameraInfo(0, "Built in"), CameraInfo(1, "USB")],
    )
    qtbot.addWidget(window)
    window.show()
    window.sensitivity.buttons[0].click()
    assert reports[-1].sensitivity == "low"
    window.sensitivity.buttons[2].click()
    assert reports[-1].sensitivity == "high"
    window.far_passersby.click()
    assert reports[-1].alert_far_passersby
    window.show_mirror.setChecked(False)
    assert not reports[-1].show_mirror and not window.corner_combo.isEnabled()
    qtbot.waitUntil(window.camera.isEnabled)
    window.camera.setCurrentIndex(1)
    window.camera.activated.emit(1)
    assert reports[-1].camera_index == 1
    assert reports[-1].camera_name == "USB"
    window.still.setValue(21)
    assert reports[-1].stationary_mute_seconds == 21.0
    window.diagnostics.setChecked(True)
    assert reports[-1].diagnostics and window.open_diagnostics.isEnabled()


def test_contain_center_and_letterbox_click_does_not_pick(qtbot: QtBot) -> None:
    view = MirrorView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.show()
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    target = view._image_rect()
    assert target is not None
    assert target.center().y() == pytest.approx(view.rect().center().y(), abs=1)
    picked: list[tuple[float, float]] = []
    view.picked.connect(lambda x, y: picked.append((x, y)))
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(200, 10))
    assert picked == []
    qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
    assert picked[0] == pytest.approx((0.5, 0.5), abs=0.01)


def test_motion_off_during_track_exit_and_hidden_updates_stop(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    view = MirrorView()
    qtbot.addWidget(view)
    view.show()
    view.set_snapshot(_frame(Status.ALERT))
    qtbot.wait(160)
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    assert view.animations_running
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    assert not view.animations_running
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    view.hide()
    view.set_snapshot(_frame(Status.ALERT))
    assert not view.animations_running


def test_popup_child_click_cancels_linger_and_all_surface_motion(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from desk_sentry.ui.overlays import MirrorPopup

    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    popup = MirrorPopup()
    qtbot.addWidget(popup)
    popup.update_snapshot(_frame(Status.ALERT))
    popup.update_snapshot(_frame(Status.CLEAR, tracks=False))
    assert popup.isVisible()
    qtbot.mouseClick(popup._view, Qt.MouseButton.LeftButton)
    assert not popup.isVisible()
    assert not popup._hide_timer.isActive()
    assert not any(
        animation.state() == QAbstractAnimation.State.Running
        for animation in popup.findChildren(QAbstractAnimation)
    )


def test_alert_without_tracks_keeps_border_animation_until_clear(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    view = MirrorView()
    qtbot.addWidget(view)
    view.show()
    view.set_snapshot(_frame(Status.ALERT, tracks=False))
    qtbot.wait(250)
    assert view.animations_running
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    qtbot.waitUntil(lambda: not view.animations_running, timeout=1000)


def test_drawer_animation_tracks_resize_during_open_and_close(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    window = MainWindow(Settings(), lambda _: None)
    qtbot.addWidget(window)
    window.resize(820, 540)
    window.show()
    window.open_drawer()
    qtbot.wait(40)
    window.resize(700, 480)
    qtbot.waitUntil(lambda: window._drawer_animation is None, timeout=1000)
    assert window._panel.geometry().right() + 1 == window._root.width()
    assert window._panel.height() == window._root.height()
    window.close_drawer()
    qtbot.wait(40)
    window.resize(820, 540)
    qtbot.waitUntil(lambda: window._drawer_animation is None, timeout=1000)
    assert window._panel.x() == window._root.width()
    assert not window._panel.isVisible() and not window._scrim.isVisible()


@pytest.mark.parametrize("status", [Status.PAUSED, Status.NO_CAMERA, Status.ERROR])
def test_no_image_has_no_status_pill(qtbot: QtBot, status: Status) -> None:
    view = MirrorView()
    qtbot.addWidget(view)
    view.show()
    view.set_snapshot(Snapshot(status))
    view.grab()
    assert view.status_pill_rect is None
    assert not view.chip_rects


@pytest.mark.parametrize("size", [(400, 400), (780, 400), (1200, 400)])
def test_status_pill_is_offset_from_letterboxed_image(qtbot: QtBot, size: tuple[int, int]) -> None:
    view = MirrorView()
    qtbot.addWidget(view)
    view.resize(*size)
    view.show()
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    view.grab()
    image_rect = view._image_rect()
    pill = view.status_pill_rect
    assert image_rect is not None and pill is not None
    assert pill.left() == pytest.approx(image_rect.left() + 12)
    assert pill.bottom() == pytest.approx(image_rect.bottom() - 12)


def test_focus_ring_only_for_keyboard_navigation(qtbot: QtBot) -> None:
    def accent_pixels(widget: QWidget) -> int:
        image = widget.grab().toImage()
        return sum(
            image.pixelColor(x, y).name() == "#3dd6b5"
            for x in range(image.width())
            for y in range(image.height())
        )

    window = MainWindow(
        Settings(), lambda _: None, list_cameras=lambda: [CameraInfo(0, "Built in")]
    )
    window.setStyleSheet(theme.STYLESHEET)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    assert not window.history_button.hasFocus()
    assert not any(w.property("keyboardFocus") for w in window.findChildren(QWidget))
    qtbot.keyClick(window._root, Qt.Key.Key_Tab)
    focused = QApplication.focusWidget()
    assert focused is not None and focused.property("keyboardFocus")
    assert accent_pixels(focused) > 0
    qtbot.mouseClick(focused, Qt.MouseButton.LeftButton)
    assert not focused.property("keyboardFocus")
    assert accent_pixels(focused) == 0
    window.compact_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
    assert window.compact_button.property("keyboardFocus")
    qtbot.keyClick(window.compact_button, Qt.Key.Key_Backtab)
    focused = QApplication.focusWidget()
    assert focused is not None and focused.property("keyboardFocus")
    qtbot.waitUntil(window.camera.isEnabled)
    window.camera.setFocus(Qt.FocusReason.TabFocusReason)
    assert window.camera.property("keyboardFocus")
    assert accent_pixels(window.camera) > 0


@pytest.mark.parametrize(
    ("status", "people", "visible", "text"),
    [
        (Status.PAUSED, 0, False, "畫面中沒有其他人"),
        (Status.CLEAR, 0, True, "畫面中沒有其他人"),
        (Status.ALERT, 2, True, "畫面中其他人 2"),
    ],
)
def test_information_row_copy_and_visibility(
    qtbot: QtBot, status: Status, people: int, visible: bool, text: str
) -> None:
    window = MainWindow(Settings(), lambda _: None)
    qtbot.addWidget(window)
    window.show()
    snap = Snapshot(status) if not visible else _frame(status, tracks=False)
    window.show_snapshot(replace(snap, people=people))
    QApplication.processEvents()
    assert window._info_row.isVisible() is visible
    assert window._people_stats._full_text == text


def test_mini_defaults_restore_and_resize_are_width_based(qtbot: QtBot) -> None:
    from desk_sentry.ui.mini_mirror import MiniMirror

    mini = MiniMirror()
    qtbot.addWidget(mini)
    mini.restore(None)
    assert (mini.width(), mini.height()) == (384, 216)
    mini.restore((40, 50, 320, 190))
    assert (mini.x(), mini.y(), mini.width(), mini.height()) == (40, 50, 320, 180)
    mini.show()
    for width, height in [(400, 350), (241, 999), (100, 100), (640, 200)]:
        mini.resize(width, height)
        QApplication.processEvents()
        assert mini.width() == max(240, width)
        assert mini.height() == round(mini.width() * 9 / 16)


def test_drawer_header_close_button_and_escape(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    window = MainWindow(Settings(), lambda _: None)
    qtbot.addWidget(window)
    window.resize(700, 480)
    window.show()
    window.open_drawer()
    assert window._drawer_header.isVisible() and window._drawer_header.height() == 48
    assert window.drawer_close_button.size().width() == 28
    assert window.drawer_close_button.size().height() == 28
    window.drawer_close_button.click()
    assert not window.drawer_open and not window._panel.isVisible()
    window.open_drawer()
    qtbot.keyClick(window.drawer_close_button, Qt.Key.Key_Escape)
    assert not window.drawer_open and not window._panel.isVisible()


@pytest.mark.parametrize("compact", [False, True])
def test_all_chips_are_clamped_to_image_inset(qtbot: QtBot, compact: bool) -> None:
    view = MirrorView(compact=compact, badge=True)
    qtbot.addWidget(view)
    view.resize(260, 400)
    view.show()
    tracks = (
        TrackView(1, (-0.1, -0.1, 0.1, 0.3), Role.SELF, 0.9),
        TrackView(2, (0.8, 0.0, 1.1, 0.4), Role.APPROACHING, 0.9, "長名字" * 40),
        TrackView(3, (0.2, 0.99, 0.5, 1.2), Role.KNOWN, 0.9, "小王"),
    )
    view.set_snapshot(replace(_frame(Status.APPROACHING), tracks=tracks))
    view.set_unseen(9)
    view.grab()
    image_rect = view._image_rect()
    assert image_rect is not None
    bounds = image_rect.adjusted(4, 4, -4, -4)
    assert len(view.chip_rects) >= (4 if compact else 5)
    assert all(bounds.contains(chip) for chip in view.chip_rects)


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("top", [0.55, 0.98])
def test_bottom_left_track_chip_avoids_status_pill(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, compact: bool, top: float
) -> None:
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    view = MirrorView(compact=compact, badge=True)
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.show()
    track = TrackView(1, (0.82, top, 0.92, 1.0), Role.INTRUDER, 0.9)
    view.set_snapshot(replace(_frame(Status.ALERT), tracks=(track,)))
    view.grab()
    image_rect = view._image_rect()
    pill = view.status_pill_rect
    assert image_rect is not None and pill is not None
    chips = [chip for chip in view.chip_rects if chip != pill]
    assert len(chips) == 1
    chip = chips[0]
    assert image_rect.adjusted(4, 4, -4, -4).contains(chip)
    assert not chip.intersects(pill)
    if top == 0.98:
        assert chip.bottom() == pytest.approx(pill.top() - 4)


def test_popup_mirror_keeps_status_pill_hidden(qtbot: QtBot) -> None:
    from desk_sentry.ui.overlays import MirrorPopup

    popup = MirrorPopup()
    qtbot.addWidget(popup)
    popup.update_snapshot(_frame(Status.ALERT))
    popup._view.grab()
    assert popup._view.status_pill_rect is None


def test_popup_teardown_then_retired_track_deletion_releases_mirror(qtbot: QtBot) -> None:
    """03d: deferred child deletion must not destroy its owner re-entrantly."""
    from desk_sentry.ui.overlays import MirrorPopup

    popup = MirrorPopup()
    qtbot.addWidget(popup)
    popup.update_snapshot(_frame(Status.ALERT))
    popup.update_snapshot(Snapshot(Status.CLEAR))
    qtbot.waitUntil(popup.isHidden, timeout=4000)
    popup.close()
    popup.deleteLater()
    del popup
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    view = MirrorView()
    qtbot.addWidget(view)
    # Hidden retirement queues child deletions synchronously, making the same
    # lifetime ordering as fade completion deterministic without timer races.
    view.set_snapshot(_frame(Status.ALERT))
    view.set_snapshot(_frame(Status.CLEAR, tracks=False))
    ref = weakref.ref(view)
    del view
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert ref() is None
