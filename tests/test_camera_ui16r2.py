"""Task 16r2 camera picker activation regressions."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from desk_sentry.camera_shared import CameraInfo
from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow


def test_activating_displayed_legacy_camera_saves_identity_once(qtbot: QtBot) -> None:
    saved: list[Settings] = []
    window = MainWindow(
        Settings(camera_index=7),
        saved.append,
        list_cameras=lambda: [CameraInfo(index=7, id="device-seven", name="USB Camera")],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(window.camera.isEnabled)

    assert window.camera.currentData() == (7, "device-seven", "USB Camera")
    assert saved == []  # rebuilding a valid legacy selection is not a user choice

    window.camera.activated.emit(window.camera.currentIndex())

    assert len(saved) == 1
    assert (saved[0].camera_index, saved[0].camera_id, saved[0].camera_name) == (
        7,
        "device-seven",
        "USB Camera",
    )

    window.camera.activated.emit(window.camera.currentIndex())
    assert len(saved) == 1  # exact identity is already persisted; no reconnect callback
