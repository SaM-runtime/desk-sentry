from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea
from pytestqt.qtbot import QtBot
from tools.ui_shots import _load_ui_fonts

from desk_sentry.config import Settings, load
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def _camera(index: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(index=index, name=name)


def test_old_settings_load_with_empty_camera_name(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"camera_index": 2}', encoding="utf-8")

    settings = load(path)

    assert settings.camera_name == ""
    assert settings.camera_index == 2


def test_camera_picker_loads_in_background_and_updates_name_and_index(qtbot: QtBot) -> None:
    saved: list[Settings] = []
    calls = 0

    def cameras():
        nonlocal calls
        calls += 1
        return [_camera(3, "USB Camera"), _camera(7, "會議鏡頭")]

    window = MainWindow(
        Settings(camera_name="舊鏡頭", camera_index=5),
        saved.append,
        list_cameras=cameras,
    )
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: window.camera.isEnabled() and window.camera.count() == 3)
    assert window.camera.itemText(0) == "舊鏡頭（未連接）"
    assert window.camera.currentData() == (5, "", "舊鏡頭")

    target = next(
        i for i in range(window.camera.count()) if window.camera.itemData(i) == (7, "", "會議鏡頭")
    )
    window.camera.setCurrentIndex(target)
    window.camera.activated.emit(target)

    assert (saved[-1].camera_name, saved[-1].camera_index) == ("會議鏡頭", 7)

    window.camera.showPopup()
    assert window.camera.currentText() == "會議鏡頭"
    window.camera.hidePopup()
    qtbot.waitUntil(lambda: calls == 2)


def test_empty_camera_picker_stays_enabled_for_retry(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=lambda: [])
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: window.camera.currentText().startswith("找不到攝影機"))

    assert window.camera.isEnabled()


@pytest.mark.parametrize(
    ("settings", "backend", "meeting_active", "unresponsive", "expected", "color"),
    [
        (
            Settings(),
            "shared",
            False,
            False,
            "共用中，可與視訊會議同時使用",
            theme.STATUS_COLOR[Status.CLEAR],
        ),
        (
            Settings(auto_yield_camera=True),
            "exclusive",
            False,
            False,
            "相容模式：Teams、Zoom 等會議軟體開麥克風時自動讓出",
            theme.STATUS_COLOR[Status.ALERT],
        ),
        (
            Settings(auto_yield_camera=False),
            "exclusive",
            False,
            False,
            "相容模式：開會時需先暫停 Desk Sentry",
            theme.STATUS_COLOR[Status.ALERT],
        ),
        (
            Settings(),
            "yielded",
            True,
            False,
            "已讓出給視訊會議，結束後自動恢復",
            theme.STATUS_COLOR[Status.WATCHING],
        ),
        (
            Settings(),
            "exclusive",
            True,
            False,
            "已讓出給視訊會議，結束後自動恢復",
            theme.STATUS_COLOR[Status.WATCHING],
        ),
        (
            Settings(auto_yield_camera=False),
            "exclusive",
            True,
            False,
            "相容模式：開會時需先暫停 Desk Sentry",
            theme.STATUS_COLOR[Status.ALERT],
        ),
        (
            Settings(),
            "shared",
            False,
            True,
            "攝影機沒有回應，稍後自動重試",
            theme.STATUS_COLOR[Status.ERROR],
        ),
        (
            Settings(),
            "pending",
            False,
            False,
            "連線中…",
            theme.STATUS_COLOR[Status.PAUSED],
        ),
        (
            Settings(armed=False),
            "exclusive",
            False,
            False,
            "已暫停",
            theme.STATUS_COLOR[Status.PAUSED],
        ),
    ],
)
def test_camera_backend_state_has_expected_status_copy_and_color(
    qtbot: QtBot,
    settings: Settings,
    backend: str,
    meeting_active: bool,
    unresponsive: bool,
    expected: str,
    color: str,
) -> None:
    window = MainWindow(settings, lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status(
        backend,
        backend_detail="winrt",
        meeting_active=meeting_active,
        unresponsive=unresponsive,
    )

    assert window.camera_status.text() == expected
    assert color in window.camera_status_dot.styleSheet()


def test_manual_camera_yield_has_confirmed_release_status(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    window.set_pause_reason("manual_yield")

    window.set_camera_status("exclusive", unresponsive=False, acknowledged=True)

    assert window.camera_status.text() == "已讓出給視訊會議，結束後自動恢復"
    assert theme.STATUS_COLOR[Status.WATCHING] in window.camera_status_dot.styleSheet()
    window.set_pause_reason(None)
    window.set_camera_status("manual", unresponsive=True)
    assert window.camera_status.text() == "已暫停"


def test_copy_camera_diagnostics_uses_plain_text_and_resets_label(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    window.show()

    diagnostic = (
        "版本：2.0.2\nWindows：11 24H2\n執行方式：原始碼\n"
        "camera_mode：auto\nbackend：shared\nbackend_detail：winrt\n"
        "最後錯誤類型：無\n格式：NV12 1920x1080 30.0 fps\n"
        "會議中：否\nauto_yield：是\n低調模式：否"
    )
    window.set_camera_diagnostics(diagnostic)
    window.copy_camera_diagnostics.click()

    assert QApplication.clipboard().text() == diagnostic
    assert window.copy_camera_diagnostics.text() == "已複製"
    qtbot.waitUntil(lambda: window.copy_camera_diagnostics.text() == "複製診斷資訊", timeout=2500)


def test_camera_settings_narrow_drawer_screenshot(qtbot: QtBot) -> None:
    _load_ui_fonts()
    window = MainWindow(
        Settings(camera_name="USB Camera"),
        lambda _settings: None,
        list_cameras=lambda: [_camera(0, "USB Camera")],
    )
    window.setStyleSheet(theme.STYLESHEET)
    qtbot.addWidget(window)
    window.resize(760, 560)
    window.show()
    qtbot.waitUntil(lambda: window.camera.isEnabled())
    assert window.camera.currentText() == "USB Camera"
    window.set_camera_status("shared", backend_detail="winrt")
    window.set_camera_diagnostics("版本：2.0.2\nbackend：shared\nbackend_detail：winrt")
    window.settings_button.click()
    qtbot.waitUntil(lambda: window._panel.isVisible())
    scroll = window._panel.findChild(QScrollArea, "settingsScroll")
    assert scroll is not None
    scroll.ensureWidgetVisible(window.copy_camera_diagnostics)

    evidence = Path("." + "task") / ("evidence" + "-15") / "camera-settings.png"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    assert window._panel.grab().save(str(evidence))
    assert evidence.stat().st_size > 0
    status_box = window.copy_camera_diagnostics.parentWidget()
    assert status_box is not None
    assert window.copy_camera_diagnostics.geometry().right() <= status_box.rect().right()
