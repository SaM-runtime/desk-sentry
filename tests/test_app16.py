"""TASK16 copied frame rates and repeated UI status work."""

from types import SimpleNamespace
from unittest.mock import Mock

from desk_sentry.app import _process_execution_mode
from desk_sentry.config import Settings
from desk_sentry.engine import Snapshot
from desk_sentry.sentry import Status
from desk_sentry.ui.main_window import MainWindow
from test_app_hide04 import app_with


def test_twenty_snapshots_classify_execution_mode_once(monkeypatch):
    _process_execution_mode.cache_clear()
    execution_mode = Mock(return_value="原始碼")
    monkeypatch.setattr("desk_sentry.app._execution_mode", execution_mode)
    app = app_with(Settings())
    for _ in range(20):
        app._on_snapshot(Snapshot(Status.CLEAR))
    another_app = app_with(Settings())
    another_app._on_snapshot(Snapshot(Status.CLEAR))
    assert execution_mode.call_count <= 1
    assert "執行方式：原始碼" in app.window.set_camera_diagnostics.call_args.args[0]
    _process_execution_mode.cache_clear()


def test_identical_camera_status_does_not_rewrite_widgets(qtbot, monkeypatch):
    window = MainWindow(Settings(), lambda _settings: None, list_cameras=list)
    qtbot.addWidget(window)
    window.set_camera_status("shared")
    text = Mock(wraps=window.camera_status.setText)
    accessible = Mock(wraps=window.camera_status.setAccessibleName)
    style = Mock(wraps=window.camera_status_dot.setStyleSheet)
    monkeypatch.setattr(window.camera_status, "setText", text)
    monkeypatch.setattr(window.camera_status, "setAccessibleName", accessible)
    monkeypatch.setattr(window.camera_status_dot, "setStyleSheet", style)
    for _ in range(20):
        window.set_camera_status("shared")
    text.assert_not_called()
    accessible.assert_not_called()
    style.assert_not_called()
    window.set_camera_status("exclusive")
    assert text.call_count == accessible.call_count == style.call_count == 1
    assert "相容模式" in window.camera_status.text()


def test_copied_diagnostics_distinguish_measured_and_nominal_fps():
    app = app_with(Settings(camera_id="private-device", camera_name="private-camera"))
    app._build_info = SimpleNamespace(version="2.0.2")
    app._on_snapshot(Snapshot(Status.CLEAR, camera_fps=12.5, nominal_fps=30.0))
    text = app._camera_diagnostics_text()
    assert "fps：實測 12.5／規格 30.0" in text
    assert "private-device" not in text and "private-camera" not in text
    app._on_snapshot(Snapshot(Status.CLEAR))
    assert "fps：實測 未知／規格 未知" in app._camera_diagnostics_text()
