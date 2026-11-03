"""Task 06 discreet-mode settings and main-stage presentation."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot, TrackView
from desk_sentry.sentry import Role, Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def _window(qtbot: QtBot, settings: Settings, seen: list[Settings] | None = None) -> MainWindow:
    window = MainWindow(settings, (seen if seen is not None else []).append)
    qtbot.addWidget(window)
    window.resize(1180, 640)
    window.show()
    QApplication.processEvents()
    return window


def _snapshot(status: Status = Status.ALERT, value: int = 64) -> Snapshot:
    return Snapshot(
        status,
        frame=np.full((90, 160, 3), value, dtype=np.uint8),
        tracks=(TrackView(7, (0.1, 0.2, 0.4, 0.9), Role.INTRUDER, 0.91),),
        self_zone=(0.2, 0.2, 0.8, 1.0),
        people=1,
        intruders=1,
    )


def test_discreet_settings_disable_identifying_cues_without_losing_values(qtbot: QtBot) -> None:
    seen: list[Settings] = []
    window = _window(
        qtbot,
        Settings(
            discreet=True,
            discreet_cue="dot",
            discreet_corner="bottom-left",
            show_mirror=True,
            edge_flash=True,
            sound=True,
            mirror_hotkey="Ctrl+Alt+M",
        ),
        seen,
    )

    assert window.discreet.isChecked()
    assert window.discreet_cue.currentData() == "dot"
    assert window.discreet_corner.currentData() == "bottom-left"
    assert window.discreet_corner_row.isVisibleTo(window)
    for row, notice in window.discreet_disabled_rows:
        assert not row.isEnabled()
        assert notice.isVisibleTo(window)
        assert notice.text() == "低調模式開啟中"
    assert not window.compact_button.isEnabled()
    assert window.settings.show_mirror and window.settings.edge_flash and window.settings.sound

    window.discreet.click()

    assert seen[-1].discreet is False
    assert window.show_mirror_row.isEnabled()
    assert window.edge_flash_row.isEnabled()
    assert window.sound_row.isEnabled()
    assert window.small_mirror_row.isEnabled()
    assert window.compact_button.isEnabled()
    assert all(not notice.isVisible() for _, notice in window.discreet_disabled_rows)
    assert window.settings.show_mirror and window.settings.edge_flash and window.settings.sound


def test_discreet_cue_dependency_keeps_focus_and_wheel_behavior(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(discreet=True, discreet_cue="dot"))
    content = window.findChild(QWidget, "settingsContent")
    scroll = window.findChild(QScrollArea, "settingsScroll")
    assert content is not None and scroll is not None
    assert window.focusWidget() is content

    window.discreet_cue.setCurrentIndex(window.discreet_cue.findData("line"))
    assert not window.discreet_corner_row.isVisible()
    window.discreet_cue.setCurrentIndex(window.discreet_cue.findData("dot"))
    assert window.discreet_corner_row.isVisibleTo(window)

    window.resize(1180, 420)
    QApplication.processEvents()
    scroll.verticalScrollBar().setValue(80)
    window.discreet_cue.setFocus()
    before = window.discreet_cue.currentIndex()
    position = scroll.verticalScrollBar().value()
    local = window.discreet_cue.rect().center()
    event = QWheelEvent(
        QPointF(local),
        QPointF(window.discreet_cue.mapToGlobal(local)),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(window.discreet_cue, event)
    assert window.discreet_cue.currentIndex() == before
    assert scroll.verticalScrollBar().value() > position


def test_discreet_stage_drops_live_and_cached_images_until_a_new_normal_frame(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(discreet=False))
    window.show_snapshot(_snapshot(value=32))
    assert window.mirror._image is not None
    assert window.mirror._tracks
    assert window._has_frame
    assert window._info_row.isVisibleTo(window)

    window.update_settings(discreet=True)
    assert window.mirror._image is None
    assert window.mirror._tracks == {}
    assert window.mirror.accessibleName() == "偵測中 · 畫面已隱藏"
    assert not window._has_frame
    assert not window._info_row.isVisible()

    private_snapshot = _snapshot(Status.APPROACHING, value=224)
    private_snapshot = replace(
        private_snapshot,
        alert_thumbnail=np.full((24, 32, 3), 192, dtype=np.uint8),
        alert_face=np.ones(8, dtype=np.float32),
    )
    window.show_snapshot(private_snapshot)
    assert window.mirror._image is None
    assert window.mirror._tracks == {}
    assert window.mirror._snapshot.frame is None
    assert window.mirror._snapshot.tracks == ()
    assert window.mirror._snapshot.self_zone is None
    assert window.mirror._snapshot.alert_thumbnail is None
    assert window.mirror._snapshot.alert_face is None
    assert window._last_tracks == ()
    assert not window._has_frame
    assert not window._info_row.isVisible()
    assert window.mirror._border_target.name() == theme.STATUS_COLOR[Status.APPROACHING].lower()

    window.update_settings(discreet=False)
    assert window.mirror._image is None
    assert not window._has_frame
    assert not window._info_row.isVisible()
    window.show_snapshot(_snapshot(Status.CLEAR, value=96))
    assert window.mirror._image is not None
    assert window.mirror._tracks
    assert window._has_frame
    assert window._info_row.isVisibleTo(window)


def test_discreet_window_uses_neutral_title_and_icon_then_restores_identity(qtbot: QtBot) -> None:
    window = _window(qtbot, Settings(discreet=True))
    neutral = theme.neutral_icon().pixmap(32, 32).toImage()
    paused_branded = theme.app_icon(theme.STATUS_COLOR[Status.PAUSED]).pixmap(32, 32).toImage()

    assert neutral != paused_branded
    assert window.windowTitle() == "設定"
    assert not window._toolbar_title.isVisible()
    assert window.windowIcon().pixmap(32, 32).toImage() == neutral
    window.show_snapshot(_snapshot(Status.ALERT))
    assert window.windowIcon().pixmap(32, 32).toImage() == neutral

    window.update_settings(discreet=False)
    assert window.windowTitle() == "Desk Sentry 後照鏡"
    assert window._toolbar_title.isVisibleTo(window)
    alert_branded = theme.app_icon(theme.STATUS_COLOR[Status.ALERT]).pixmap(32, 32).toImage()
    assert window.windowIcon().pixmap(32, 32).toImage() == alert_branded
