"""Task 16r camera picker and status wording regressions."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from desk_sentry.camera_shared import CameraInfo
from desk_sentry.config import Settings
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow


def _camera(index: int, camera_id: str, name: str) -> CameraInfo:
    return CameraInfo(index=index, id=camera_id, name=name)


def test_camera_picker_matches_stored_id_case_insensitively_before_nearest_name(
    qtbot: QtBot,
) -> None:
    window = MainWindow(
        Settings(camera_id="DEVICE-B", camera_name="USB Camera", camera_index=0),
        lambda _settings: None,
        list_cameras=lambda: [
            _camera(0, "device-a", "USB Camera"),
            _camera(7, "device-b", "USB Camera"),
        ],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(window.camera.isEnabled)

    assert window.camera.currentData() == (7, "device-b", "USB Camera")


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("locked", "已暫停 · 電腦鎖定中"),
        ("schedule", "已暫停 · 非監看時段"),
    ],
)
def test_automatic_pause_backend_has_specific_camera_status(
    qtbot: QtBot, backend: str, expected: str
) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status(backend)

    assert window.camera_status.text() == expected
    assert theme.STATUS_COLOR[Status.PAUSED] in window.camera_status_dot.styleSheet()


def test_explicit_release_failure_has_priority_and_recovers_to_transient_error(
    qtbot: QtBot,
) -> None:
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)

    window.set_camera_status(
        "locked",
        release_failed=True,
        last_error="CameraDisconnectedError",
        unresponsive=True,
    )
    assert (
        window.camera_status.text()
        == "無法確認已讓出攝影機；若會議軟體拿不到畫面，請結束 Desk Sentry"
    )
    assert theme.STATUS_COLOR[Status.ERROR] in window.camera_status_dot.styleSheet()

    window.set_camera_status("pending", release_failed=False, unresponsive=True)
    assert window.camera_status.text() == "攝影機沒有回應，稍後自動重試"


@pytest.mark.parametrize(
    ("auto_yield", "expected"),
    [
        (True, "相容模式：Teams、Zoom 等會議軟體開麥克風時自動讓出"),
        (False, "相容模式：開會時需先暫停 Desk Sentry"),
    ],
)
def test_exclusive_mode_explains_automatic_or_manual_yield(
    qtbot: QtBot, auto_yield: bool, expected: str
) -> None:
    window = MainWindow(
        Settings(camera_mode="exclusive", auto_yield_camera=auto_yield),
        lambda _settings: None,
        list_cameras=list,
    )
    qtbot.addWidget(window)

    window.set_camera_status("exclusive")

    assert window.camera_status.text() == expected
