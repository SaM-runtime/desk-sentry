"""Task09c regressions: installed cached versions and cancelable shutdown."""

import ctypes
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from desk_sentry import app as app_module
from desk_sentry import config, lifecycle, schedule
from desk_sentry.build_info import BuildInfo
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.project import GITHUB_REPOSITORY
from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from test_app_review05c import local_app as local_app


@pytest.mark.parametrize(
    ("cached", "current", "status", "automatic", "expected"),
    [
        ("2.1.0", "2.1.0", "available", False, "latest"),
        ("2.1.0", "2.2.0", "available", False, "latest"),
        ("2.1.0", "2.0.0", "available", False, "available"),
        ("2.1.0", "2.1.0+abc", "available", False, "latest"),
        ("2.1.0", "2.1.0", "ignored", False, "latest"),
        ("2.1.0", "2.2.0", "ignored", False, "latest"),
        ("2.1.0", "2.0.0", "ignored", False, "ignored"),
        ("2.1.0", "2.1.0+abc", "ignored", False, "latest"),
        ("2.1.0", "2.1.0", "available", True, "latest"),
        ("2.9.0", "2.10.0", "available", False, "latest"),
    ],
)
def test_cached_update_is_reconciled_with_installed_version_before_restore(
    local_app, qtbot, monkeypatch, cached, current, status, automatic, expected
):
    base, _, commit = current.partition("+")
    monkeypatch.setattr(
        app_module,
        "get_build_info",
        lambda: BuildInfo(base, commit or "abc1234", False, "today", not commit),
    )
    network = Mock(side_effect=AssertionError("cached reconciliation must not connect"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    build, path, _ = local_app
    checked_at = time.time()
    config.save(
        replace(
            config.load(path),
            update_check_enabled=automatic,
            update_last_checked=checked_at,
            update_last_status=status,
            update_latest_version=cached,
            update_ignored_version=cached if status == "ignored" else "",
            update_release_url=f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/v{cached}",
            _extra_settings={"future_option": {"keep": True}},
        ),
        path,
    )
    for _ in range(2):
        instance = build()
        qtbot.waitUntil(lambda instance=instance: not instance._update_busy, timeout=3000)
        assert instance.window.settings.update_last_status == expected
        assert config.load(path).update_last_status == expected
        assert config.load(path).update_last_checked == checked_at
        assert config.load(path)._extra_settings == {"future_option": {"keep": True}}
        offered = expected == "available"
        assert instance._update_action.isVisible() is offered
        assert instance._skip_update_action.isVisible() is offered
        if not offered:
            assert instance._available_update is None
            unmarked = theme.app_icon(theme.STATUS_COLOR[Status.PAUSED]).pixmap(32, 32).toImage()
            assert instance.tray.icon().pixmap(32, 32).toImage() == unmarked
            if expected == "latest" and not commit:
                assert instance.window.update_status_label.text().startswith("已是最新版")
    network.assert_not_called()


@pytest.mark.parametrize("cancel", [False, True], ids=["query-only", "query-then-cancel"])
def test_session_query_and_cancel_keep_the_engine_monitoring(local_app, qtbot, monkeypatch, cancel):
    flushed = []
    original_flush = DiagnosticsLog.flush

    def flush(log):
        flushed.append(log)
        original_flush(log)

    monkeypatch.setattr(DiagnosticsLog, "flush", flush)
    build, _, started = local_app
    instance = build()
    requested = []

    def quit_app():
        requested.append(True)
        instance.quit()

    event_filter = lifecycle.EndSessionFilter(
        int(instance.window.winId()), quit_app, shutdown_now=instance.shutdown_now
    )
    message = schedule._Message()
    message.hwnd = int(instance.window.winId())
    message.message = lifecycle.WM_QUERYENDSESSION
    count_before = len(flushed)
    assert event_filter.nativeEventFilter(None, ctypes.addressof(message)) == (True, 1)
    if cancel:
        message.message = lifecycle.WM_ENDSESSION
        message.w_param = 0
        event_filter.nativeEventFilter(None, ctypes.addressof(message))
    qtbot.wait(20)
    assert requested == []
    assert not instance._quitting
    assert not instance.window.allow_close
    assert instance.tray.shown
    assert instance.engine in started
    assert not instance.engine._stop.is_set()
    assert instance._effective_armed
    assert len(flushed) == count_before
