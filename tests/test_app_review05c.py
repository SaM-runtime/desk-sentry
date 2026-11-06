"""Startup storage failure and clock changes must preserve monitoring intent."""

import ctypes
import gzip
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QSystemTrayIcon

from desk_sentry import app as app_module
from desk_sentry import config, hotkey, schedule
from desk_sentry.app import DeskSentryApp
from desk_sentry.config import Settings
from desk_sentry.engine import Engine
from desk_sentry.stats import AlertStats
from test_hotkey04 import FakeBackend as HotkeyBackend
from test_session05 import FakeApp, dispatch
from test_session05 import FakeBackend as SessionBackend

WM_TIMECHANGE = 0x001E


class LocalTray(QSystemTrayIcon):
    """Keep real Qt actions/menus without displaying a test icon on the desktop."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []

    def showMessage(self, title, text, icon, timeout):  # noqa: N802 - Qt boundary
        self.messages.append((title, text))

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False


@pytest.fixture
def local_app(qapp, qtbot, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(app_module, "QSystemTrayIcon", LocalTray)
    monkeypatch.setattr(
        app_module,
        "PersonDetector",
        lambda _path: SimpleNamespace(detect=lambda _frame, **_kwargs: []),
    )
    monkeypatch.setattr(Engine, "start", lambda engine: started.append(engine))
    monkeypatch.setattr(hotkey, "Win32Backend", lambda _id: HotkeyBackend())
    monkeypatch.setattr(schedule, "Win32SessionBackend", SessionBackend)
    monkeypatch.setattr(
        app_module.meeting,
        "WinRegistryReader",
        lambda: SimpleNamespace(entries=lambda _capability: ()),
    )
    monkeypatch.setattr(app_module.startup, "is_enabled", lambda: False)
    monkeypatch.setattr(app_module.actions, "list_app_windows", lambda: [])
    path = tmp_path / "settings.json"
    config.save(Settings(diagnostics=True), path)
    instances = []

    def build(*, start_hidden=True):
        instance = DeskSentryApp(qapp, path, start_hidden=start_hidden)
        instances.append(instance)
        for widget in (instance.window, instance.mini, instance.popup):
            qtbot.addWidget(widget)
        return instance

    yield build, path, started
    for instance in instances:
        monitor = instance._meeting_monitor
        worker = monitor._thread
        monitor.stop()
        assert worker is None or not worker.is_alive()
        qapp.aboutToQuit.disconnect(monitor.stop)
        cue = getattr(instance, "discreet_cue", None)
        if cue is not None:
            cue.close()
            qapp.aboutToQuit.disconnect(cue.close)
            cue.deleteLater()
        instance.window.allow_close = True
        instance._schedule_timer.stop()
        resync = getattr(instance, "_schedule_resync_timer", None)
        if resync is not None:
            resync.stop()
            qapp.aboutToQuit.disconnect(resync.stop)
            resync.deleteLater()
        instance._schedule_timer.deleteLater()
        instance.hotkey.close()
        instance.hide_hotkey.close()
        instance._session_notifications.close()
        instance.tray.hide()
        instance.tray.deleteLater()
        qapp.aboutToQuit.disconnect(instance.shutdown_now)
        qapp.aboutToQuit.disconnect(instance._session_notifications.close)


@pytest.mark.parametrize("error_type", [OSError, PermissionError])
def test_startup_stats_write_failure_still_creates_tray_and_engine(
    local_app, monkeypatch, error_type
):
    build, path, started = local_app
    stats = AlertStats(path.parent / "alert-stats.json")
    stats.save({"2020-01-01": (1,) + (0,) * 23})

    def fail_save(_stats, _data):
        raise error_type("injected startup stats write failure")

    monkeypatch.setattr(AlertStats, "save", fail_save)
    instance = build()

    assert instance.tray.shown
    assert isinstance(instance.engine, Engine)
    assert started == [instance.engine]
    assert instance.window.settings.armed
    instance._diagnostics.flush()
    records = [
        json.loads(line)
        for file in instance._diagnostics.folder.glob("*.jsonl.gz")
        for line in gzip.decompress(file.read_bytes()).decode("utf-8").splitlines()
    ]
    failures = [record for record in records if record["kind"] == "stats_error"]
    assert len(failures) == 1
    assert "injected startup stats write failure" in failures[0]["error"]


def send_native(instance, message_id, event=0, *, hwnd=None):
    message = schedule._Message()
    message.hwnd = int(instance.window.winId()) if hwnd is None else hwnd
    message.message = message_id
    message.w_param = event
    return instance._session_notifications._filter.nativeEventFilter(
        None, ctypes.addressof(message)
    )


def test_timechange_immediately_resyncs_effective_monitoring_and_boundary(local_app):
    build, _path, _started = local_app
    instance = build()
    instance.window.settings.schedule_enabled = True
    now = datetime(2026, 10, 12, 12)
    instance._wall_now = lambda: now
    instance._apply_monitoring()
    assert instance._effective_armed
    assert instance._schedule_timer.interval() == 23_400_000

    now = datetime(2026, 10, 12, 23)
    send_native(instance, WM_TIMECHANGE)

    assert not instance._effective_armed
    assert instance._pause_reason == "schedule"
    assert instance.window.settings.armed
    assert instance._schedule_timer.interval() == 34_200_000  # next day 08:30
    assert instance._pause_info.isVisible()
    assert instance.window.mirror.pause_detail == "非監看時段 · 週二 08:30 自動開始"


def test_resync_timer_recomputes_after_clock_jump_without_changing_user_armed(local_app):
    build, _path, _started = local_app
    instance = build()
    timer = instance._schedule_resync_timer
    assert timer.isActive()
    assert not timer.isSingleShot()
    assert timer.interval() == 300_000 == app_module.SCHEDULE_RESYNC_MS
    instance.window.settings.schedule_enabled = True
    now = datetime(2026, 10, 12, 12)
    instance._wall_now = lambda: now
    instance._apply_monitoring()
    assert instance._effective_armed

    now = datetime(2026, 10, 12, 23)
    timer.timeout.emit()

    assert not instance._effective_armed
    assert instance.window.settings.armed
    assert instance._schedule_timer.interval() == 34_200_000
    assert timer.isActive()


def test_timechange_and_wts_share_one_native_entry_and_cleanup():
    native_app = FakeApp()
    backend = SessionBackend()
    locked = []
    changed = []
    notifications = app_module._MonitoringNotifications(
        locked.append, lambda: changed.append(True), backend
    )
    assert notifications.install(native_app, 1234)
    assert notifications.install(native_app, 1234)
    assert len(native_app.filters) == 1

    dispatch(native_app, 9999, WM_TIMECHANGE, 0)
    dispatch(native_app, 1234, WM_TIMECHANGE, 0)
    dispatch(native_app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_LOCK)
    dispatch(native_app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_UNLOCK)
    assert changed == [True]
    assert locked == [True, False]
    notifications.close()
    assert native_app.filters == []
    assert backend.unregistered == [1234]
