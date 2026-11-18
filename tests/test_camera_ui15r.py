from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea
from pytestqt.qtbot import QtBot
from tools.ui_shots import _load_ui_fonts

from desk_sentry.camera_shared import CameraInfo
from desk_sentry.config import Settings, load
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def _camera(index: int, camera_id: str, name: str) -> CameraInfo:
    return CameraInfo(index=index, name=name, id=camera_id)


def test_camera_id_defaults_and_invalid_values_normalize_to_empty(tmp_path) -> None:
    old_path = tmp_path / "old.json"
    old_path.write_text('{"camera_index": 2}', encoding="utf-8")

    assert load(old_path).camera_id == ""
    assert Settings(camera_id=object()).normalized().camera_id == ""


def test_duplicate_names_show_ordinal_and_selection_persists_identity(qtbot: QtBot) -> None:
    saved: list[Settings] = []
    window = MainWindow(
        Settings(camera_id="device-b", camera_name="USB Camera", camera_index=1),
        saved.append,
        list_cameras=lambda: [
            _camera(0, "device-a", "USB Camera"),
            _camera(1, "device-b", "USB Camera"),
        ],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.camera.isEnabled() and window.camera.count() == 2)

    assert [window.camera.itemText(i) for i in range(2)] == ["USB Camera", "USB Camera (2)"]
    assert window.camera.currentData() == (1, "device-b", "USB Camera")

    window.camera.setCurrentIndex(0)
    window.camera.activated.emit(0)

    assert (saved[-1].camera_id, saved[-1].camera_name, saved[-1].camera_index) == (
        "device-a",
        "USB Camera",
        0,
    )


def test_missing_legacy_index_saves_first_available_camera(qtbot: QtBot) -> None:
    saved: list[Settings] = []
    window = MainWindow(
        Settings(camera_index=9),
        saved.append,
        list_cameras=lambda: [_camera(3, "device-a", "內建攝影機")],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: bool(saved))

    assert (saved[-1].camera_id, saved[-1].camera_name, saved[-1].camera_index) == (
        "device-a",
        "內建攝影機",
        3,
    )
    assert window.camera.currentData() == (3, "device-a", "內建攝影機")


def test_stale_id_falls_back_to_nearest_same_name_camera(qtbot: QtBot) -> None:
    window = MainWindow(
        Settings(camera_id="stale", camera_name="USB Camera", camera_index=8),
        lambda _settings: None,
        list_cameras=lambda: [
            _camera(1, "device-a", "USB Camera"),
            _camera(7, "device-b", "USB Camera"),
        ],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(window.camera.isEnabled)

    assert window.camera.currentData() == (7, "device-b", "USB Camera")


def test_missing_stored_identity_stays_selected_as_disconnected(qtbot: QtBot) -> None:
    saved: list[Settings] = []
    window = MainWindow(
        Settings(camera_id="missing-id", camera_name="舊鏡頭", camera_index=5),
        saved.append,
        list_cameras=lambda: [_camera(0, "device-a", "內建攝影機")],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.camera.isEnabled() and window.camera.count() == 2)

    assert window.camera.currentText() == "舊鏡頭（未連接）"
    assert window.camera.currentData() == (5, "missing-id", "舊鏡頭")
    assert saved == []


def test_empty_list_remains_clickable_and_retries_until_found(qtbot: QtBot) -> None:
    responses = [[], [], [_camera(4, "device-c", "外接鏡頭")]]

    def cameras():
        return responses.pop(0)

    saved: list[Settings] = []
    window = MainWindow(Settings(), saved.append, list_cameras=cameras)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.camera.currentText().startswith("找不到攝影機"))

    assert window.camera.isEnabled()
    assert window._camera_retry_timer.interval() == 10_000
    assert window._camera_retry_timer.isActive()

    window.camera.showPopup()
    window.camera.hidePopup()
    qtbot.waitUntil(lambda: len(responses) == 1)
    window._camera_retry_timer.start(10)
    qtbot.waitUntil(lambda: window.camera.currentText() == "外接鏡頭")

    assert not window._camera_retry_timer.isActive()
    assert saved[-1].camera_id == "device-c"


def test_empty_list_preserves_stored_disconnected_selection_while_retrying(qtbot: QtBot) -> None:
    window = MainWindow(
        Settings(camera_id="missing-id", camera_name="舊鏡頭", camera_index=5),
        lambda _settings: None,
        list_cameras=list,
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.camera.currentText() == "舊鏡頭（未連接）")

    assert window.camera.isEnabled()
    assert window._camera_retry_timer.isActive()
    assert window.camera.currentData() == (5, "missing-id", "舊鏡頭")


@pytest.mark.parametrize(
    (
        "reason",
        "backend",
        "acknowledged",
        "last_error",
        "unresponsive",
        "expected",
        "color",
    ),
    [
        ("meeting", "exclusive", False, None, False, "正在讓出攝影機…", Status.PAUSED),
        (
            "meeting",
            "exclusive",
            True,
            None,
            False,
            "已讓出給視訊會議，結束後自動恢復",
            Status.WATCHING,
        ),
        (
            "manual_yield",
            "manual",
            False,
            None,
            False,
            "正在讓出攝影機…",
            Status.PAUSED,
        ),
        (
            "manual_yield",
            "manual",
            True,
            None,
            False,
            "已讓出給視訊會議，結束後自動恢復",
            Status.WATCHING,
        ),
        (
            None,
            "pending",
            True,
            "CameraDisconnectedError",
            False,
            "選擇的攝影機未連接",
            Status.ERROR,
        ),
        (
            "meeting",
            "exclusive",
            False,
            "CameraReleaseError",
            True,
            "無法讓出攝影機，稍後自動重試",
            Status.ERROR,
        ),
        (
            "manual_yield",
            "manual",
            False,
            None,
            True,
            "無法讓出攝影機，稍後自動重試",
            Status.ERROR,
        ),
    ],
)
def test_release_ack_and_camera_errors_have_distinct_statuses(
    qtbot: QtBot,
    reason: str | None,
    backend: str,
    acknowledged: bool,
    last_error: str | None,
    unresponsive: bool,
    expected: str,
    color: Status,
) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    window.set_pause_reason(reason, acknowledged=acknowledged)

    window.set_camera_status(
        backend,
        last_error=last_error,
        meeting_active=reason == "meeting",
        unresponsive=unresponsive,
        acknowledged=acknowledged,
    )

    assert window.camera_status.text() == expected
    assert theme.STATUS_COLOR[color] in window.camera_status_dot.styleSheet()


def test_historical_disconnect_error_does_not_override_success(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status("shared", last_error="CameraDisconnectedError")

    assert window.camera_status.text() == "共用中，可與視訊會議同時使用"


def test_shared_camera_during_meeting_does_not_show_release_state(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status("shared", meeting_active=True, acknowledged=False)

    assert window.camera_status.text() == "共用中，可與視訊會議同時使用"


def test_manual_backend_without_yield_request_is_paused(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status("manual", acknowledged=True)

    assert window.camera_status.text() == "已暫停"


def test_stale_release_error_without_current_failure_stays_pending(qtbot: QtBot) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    window.set_pause_reason("meeting", acknowledged=False)

    window.set_camera_status(
        "exclusive",
        last_error="CameraReleaseError",
        meeting_active=True,
        acknowledged=False,
        unresponsive=False,
    )

    assert window.camera_status.text() == "正在讓出攝影機…"


def test_camera_identity_is_not_added_to_copied_diagnostics(qtbot: QtBot) -> None:
    camera_id = "private-device-identifier"
    window = MainWindow(
        Settings(),
        lambda _settings: None,
        list_cameras=lambda: [_camera(0, camera_id, "USB Camera")],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(window.camera.isEnabled)
    window.set_camera_diagnostics("版本：2.0.2\nbackend：shared")

    window.copy_camera_diagnostics.click()

    assert camera_id not in QApplication.clipboard().text()


def test_camera_15r_narrow_drawer_screenshot(qtbot: QtBot) -> None:
    _load_ui_fonts()
    window = MainWindow(
        Settings(camera_id="device-b", camera_name="USB Camera", camera_index=1),
        lambda _settings: None,
        list_cameras=lambda: [
            _camera(0, "device-a", "USB Camera"),
            _camera(1, "device-b", "USB Camera"),
        ],
    )
    window.setStyleSheet(theme.STYLESHEET)
    qtbot.addWidget(window)
    window.resize(760, 560)
    window.show()
    qtbot.waitUntil(lambda: window.camera.currentText() == "USB Camera (2)")
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
