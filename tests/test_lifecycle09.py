"""Public IPC and session shutdown routes must flush through aboutToQuit."""

import ctypes
import os
import subprocess
import sys
import uuid

import pytest
from PySide6.QtCore import QTimer

from desk_sentry import app as app_module
from desk_sentry import lifecycle, schedule
from desk_sentry.diagnostics import DiagnosticsLog
from test_app_review05c import local_app as local_app


def test_named_mutex_rejects_second_owner_and_releases_for_next_instance():
    name = f"DeskSentry.Test.{uuid.uuid4().hex}"
    first, second = lifecycle.SingleInstanceMutex(name), lifecycle.SingleInstanceMutex(name)
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        first.close()
        second.close()
    third = lifecycle.SingleInstanceMutex(name)
    try:
        assert third.acquire()
    finally:
        third.close()


@pytest.mark.parametrize(
    "route", ["ipc", "query_then_end_session", "end_session", "restart_manager"]
)
def test_shutdown_notification_runs_about_to_quit_and_flushes(
    local_app, qapp, monkeypatch, tmp_path, route
):
    # A real application starts cold. Re-entering app.exec() with other tests'
    # global QApplication/native filters can hang Qt teardown, so isolate the
    # shutdown infrastructure and put a hard deadline around the child process.
    if os.environ.get("DESKSENTRY_SHUTDOWN_PROBE") != route:
        env = os.environ.copy()
        env.pop("PYTEST_ADDOPTS", None)
        env["DESKSENTRY_SHUTDOWN_PROBE"] = route
        probe = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(tmp_path / "cold-process"),
                f"{__file__}::test_shutdown_notification_runs_about_to_quit_and_flushes[{route}]",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            stdout, stderr = probe.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(probe.pid), "/T", "/F"], capture_output=True, timeout=10
            )
            pytest.fail("cold shutdown probe timed out")
        assert probe.returncode == 0, f"{stdout}\n{stderr}"
        return
    flushed = []
    original_flush = DiagnosticsLog.flush

    def flush(log):
        flushed.append(log)
        original_flush(log)

    monkeypatch.setattr(DiagnosticsLog, "flush", flush)
    build, _, _ = local_app
    instance = build()
    quit_calls = []
    original_quit = instance.quit

    def quit_app():
        quit_calls.append(True)
        original_quit()

    monkeypatch.setattr(instance, "quit", quit_app)
    monkeypatch.setattr(app_module, "INSTANCE_NAME", f"desk-sentry-test-{uuid.uuid4().hex}")
    server = app_module._listen_for_other_instances(instance)
    event_filter = lifecycle.EndSessionFilter(
        int(instance.window.winId()), instance.quit, shutdown_now=instance.shutdown_now
    )
    received = []
    sender = None

    def notify():
        nonlocal sender
        if route == "ipc":
            # Match the separate CLI process: its blocking socket calls must not
            # prevent the running application's event loop from receiving data.
            sender = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys; from PySide6.QtCore import QCoreApplication; "
                    "from desk_sentry import app; core=QCoreApplication([]); "
                    "app.INSTANCE_NAME=sys.argv[1]; "
                    "raise SystemExit(0 if app._signal_running_instance(b'quit') else 1)",
                    app_module.INSTANCE_NAME,
                ]
            )
        else:
            message = schedule._Message()
            message.hwnd = int(instance.window.winId())
            if route in ("query_then_end_session", "restart_manager"):
                message.message = lifecycle.WM_QUERYENDSESSION
                assert event_filter.nativeEventFilter(None, ctypes.addressof(message)) == (True, 1)
                assert quit_calls == []
            message.message = lifecycle.WM_ENDSESSION
            message.w_param = 1
            message.l_param = 0x00000001 if route == "restart_manager" else 0
            received.append(event_filter.nativeEventFilter(None, ctypes.addressof(message)))
            # Duplicate session completion must not dispatch a second quit.
            assert event_filter.nativeEventFilter(None, ctypes.addressof(message)) == (True, 0)

    QTimer.singleShot(0, notify)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(qapp.quit)
    watchdog.start(10000)
    try:
        qapp.exec()
    finally:
        watchdog.stop()
        server.close()
        if sender is not None:
            received.append(sender.wait(timeout=3) == 0)
    assert received == ([True] if route == "ipc" else [(True, 0)])
    assert quit_calls == [True]
    assert instance._diagnostics in flushed
    assert instance.engine._stop.is_set()
    assert instance.window.allow_close
    assert not instance.tray.shown


def test_quit_without_running_instance_does_not_load_settings_or_model(qapp, monkeypatch):
    monkeypatch.setattr(app_module, "INSTANCE_NAME", f"desk-sentry-test-{uuid.uuid4().hex}")
    monkeypatch.setattr(
        app_module.config, "load", lambda *_: pytest.fail("read settings for --quit")
    )
    monkeypatch.setattr(app_module, "model_path", lambda: pytest.fail("load model for --quit"))
    assert app_module.main(["--quit"]) == 0


def test_second_launch_reveals_existing_settings_window(local_app, qtbot, monkeypatch):
    build, _, _ = local_app
    instance = build()
    monkeypatch.setattr(app_module, "INSTANCE_NAME", f"desk-sentry-test-{uuid.uuid4().hex}")
    server = app_module._listen_for_other_instances(instance)
    assert not instance.window.isVisible()
    sender = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from PySide6.QtCore import QCoreApplication; "
            "from desk_sentry import app; core=QCoreApplication([]); "
            "app.INSTANCE_NAME=sys.argv[1]; "
            "raise SystemExit(0 if app._signal_running_instance() else 1)",
            app_module.INSTANCE_NAME,
        ]
    )
    try:
        qtbot.waitUntil(instance.window.isVisible, timeout=3000)
        assert sender.wait(timeout=3) == 0
    finally:
        server.close()
        if sender.poll() is None:
            sender.kill()
