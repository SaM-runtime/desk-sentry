"""Task09b user-owned update controls and persisted status contract."""

import io
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QKeySequenceEdit, QLineEdit

from desk_sentry import __version__, config, update_check
from desk_sentry import app as app_module
from desk_sentry.build_info import BuildInfo
from desk_sentry.ui import theme
from desk_sentry.ui.main_window import MainWindow
from test_app_review05c import local_app as local_app


def test_explicit_manual_check_is_one_off_consent_with_auto_disabled(monkeypatch):
    request = Mock(return_value=io.BytesIO(b'{"tag_name":"v2.1.0"}'))
    monkeypatch.setattr("urllib.request.urlopen", request)
    automatic = update_check.check_for_update("2.0.0", enabled=False, now=100)
    assert not automatic.attempted
    request.assert_not_called()
    manual = update_check.check_for_update("2.0.0", enabled=False, manual=True, now=100)
    assert manual.attempted
    assert manual.available.version == "2.1.0"
    assert request.call_count == 1


def test_workflow_project_python_calls_use_the_deploy_venv():
    root = Path(__file__).resolve().parents[1]
    for name in ("ci.yml", "release.yml"):
        text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "-m venv .venv" in text
        calls = re.findall(r'([^\s"\']*python(?:\.exe)?)["\']?\s+([^\n]+)', text)
        assert len(calls) >= 7
        for program, args in calls:
            if args.strip() == "-m venv .venv":
                assert program == "$env:pythonLocation\\python.exe"
            else:
                assert program == r".\.venv\Scripts\python.exe", (name, program, args)


@pytest.mark.parametrize(
    ("status", "checking", "display", "expected", "button", "skip"),
    [
        ("never", False, "2.0.0", "尚未檢查", "立即檢查", False),
        ("never", True, "2.0.0", "檢查中…", "立即檢查", False),
        ("latest", False, "2.0.0", "已是最新版 · 今天 15:42 檢查", "立即檢查", False),
        ("available", False, "2.0.0", "有新版 2.1.0", "前往下載", True),
        ("ignored", False, "2.0.0", "已略過 2.1.0 · 今天 15:42 檢查", "立即檢查", False),
        ("error", False, "2.0.0", "無法連線 GitHub，稍後再試", "立即檢查", False),
        (
            "development",
            False,
            "2.0.0+abc1234",
            "開發版 · 最新正式版 2.1.0",
            "立即檢查",
            False,
        ),
    ],
)
def test_seven_status_rows_follow_approved_copy(
    qtbot, status, checking, display, expected, button, skip
):
    window = MainWindow(
        config.Settings(
            update_last_status=status,
            update_latest_version="2.1.0",
            update_last_checked=datetime(2026, 10, 20, 15, 42).timestamp(),
        ),
        lambda _: None,
        display_version=display,
    )
    qtbot.addWidget(window)
    window.set_update_checking(checking)
    window.refresh_update_status(now=datetime(2026, 10, 20, 16))
    assert window.update_status_label.text() == expected
    assert window.check_updates_button.text() == button
    assert window.check_updates_button.isEnabled() is not checking
    assert window.skip_update_button.isHidden() is (not skip)
    assert window.check_updates_row.isEnabled()
    assert window.check_updates_row.graphicsEffect() is None
    assert (theme.ACCENT in window.update_status_label.styleSheet()) is (status == "available")
    window.allow_close = True


def test_prior_day_timestamp_placeholder_and_footer_alignment(qtbot):
    window = MainWindow(
        config.Settings(
            update_last_status="latest",
            update_last_checked=datetime(2026, 10, 19, 15, 42).timestamp(),
        ),
        lambda _: None,
        display_version="2.0.0",
    )
    qtbot.addWidget(window)
    window.resize(1180, 900)
    window.show()
    window.refresh_update_status(now=datetime(2026, 10, 20, 16))
    assert window.update_status_label.text() == "已是最新版 · 10/19 15:42 檢查"
    editors = window.findChildren(QKeySequenceEdit)
    assert len(editors) == 4
    for editor in editors:
        assert editor.findChild(QLineEdit).placeholderText() == "按下快捷鍵"
    assert window.privacy_label.text() == "影像只在記憶體中處理，不錄影、不存檔。"
    assert window.version_label.geometry().left() == window.privacy_label.geometry().left()
    assert window.version_label.parent() is window.privacy_label.parent()
    window.allow_close = True


@pytest.mark.parametrize("discreet", [False, True])
def test_manual_button_with_auto_off_download_skip_and_restart(
    local_app, qtbot, monkeypatch, discreet
):
    monkeypatch.setattr(
        app_module,
        "get_build_info",
        lambda: BuildInfo(__version__, "abc1234", False, "today", True),
    )
    request = Mock(side_effect=lambda *a, **k: io.BytesIO(b'{"tag_name":"v2.1.0"}'))
    monkeypatch.setattr("urllib.request.urlopen", request)
    opened = []
    monkeypatch.setattr(
        app_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    build, path, _ = local_app
    config.save(replace(config.load(path), discreet=discreet), path)
    instance = build()
    assert instance.window.check_updates_button.isEnabled()
    qtbot.wait(20)
    request.assert_not_called()
    instance.window.check_updates_button.click()
    assert instance.window.update_status_label.text() == "檢查中…"
    assert not instance.window.check_updates_button.isEnabled()
    qtbot.waitUntil(lambda: not instance._update_busy, timeout=3000)
    assert request.call_count == 1
    assert not instance.window.update_check.isChecked()
    assert instance.window.update_status_label.text() == "有新版 2.1.0"
    image = instance.tray.icon().pixmap(32, 32).toImage()
    assert image.pixelColor(27, 4).name() == (theme.TEXT_2 if discreet else theme.ACCENT).lower()
    assert instance._update_action.text() == ("有可用更新…" if discreet else "下載新版 2.1.0…")
    assert instance._skip_update_action.text() == ("略過此更新" if discreet else "略過此版本")
    assert instance._quit_action.text() == ("結束背景服務" if discreet else "結束 Desk Sentry")
    if discreet:
        assert all("Desk Sentry" not in action.text() for action in instance._menu.actions())
    instance.window.check_updates_button.click()
    assert opened and opened[0].endswith("/releases/tag/v2.1.0")
    assert request.call_count == 1
    restarted = build()
    assert restarted.window.update_status_label.text() == "有新版 2.1.0"
    assert restarted._update_action.isVisible()
    restarted.window.skip_update_button.click()
    assert config.load(path).update_last_status == "ignored"
    assert config.load(path).update_ignored_version == "2.1.0"
    again = build()
    assert again.window.update_status_label.text().startswith("已略過 2.1.0 · ")
    assert not again._update_action.isVisible()
    assert request.call_count == 1
    assert instance.tray.messages == restarted.tray.messages == again.tray.messages == []


def test_error_persists_and_manual_development_only_shows_latest(local_app, qtbot, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "get_build_info",
        lambda: BuildInfo(__version__, "abc1234", False, "today", False),
    )
    request = Mock(side_effect=TimeoutError("offline"))
    monkeypatch.setattr("urllib.request.urlopen", request)
    build, path, _ = local_app
    instance = build()
    instance.window.update_check.setChecked(True)
    qtbot.waitUntil(lambda: not instance._update_busy, timeout=3000)
    request.assert_not_called()
    instance.window.check_updates_button.click()
    qtbot.waitUntil(lambda: not instance._update_busy, timeout=3000)
    assert instance.window.update_status_label.text() == "無法連線 GitHub，稍後再試"
    assert config.load(path).update_last_status == "error"
    restarted = build()
    qtbot.waitUntil(lambda: not restarted._update_busy, timeout=3000)
    assert restarted.window.update_status_label.text() == "無法連線 GitHub，稍後再試"
    assert request.call_count == 1
    request.side_effect = lambda *a, **k: io.BytesIO(b'{"tag_name":"v2.1.0"}')
    restarted.window.check_updates_button.click()
    qtbot.waitUntil(lambda: not restarted._update_busy, timeout=3000)
    assert restarted.window.update_status_label.text() == "開發版 · 最新正式版 2.1.0"
    assert not restarted._update_action.isVisible()
    assert request.call_args.args[0].get_header("User-agent") == "DeskSentry/2.0.0"


@pytest.mark.parametrize(
    "status", ["never", "latest", "available", "ignored", "error", "development"]
)
def test_update_result_fields_survive_config_reload(tmp_path, status):
    path = tmp_path / "settings.json"
    settings = config.Settings(
        update_last_status=status,
        update_latest_version="2.1.0",
        update_last_checked=1234.0,
        update_release_url="https://github.com/owner/repo/releases/tag/v2.1.0",
    )
    config.save(settings, path)
    loaded = config.load(path)
    assert loaded.update_last_status == status
    assert loaded.update_latest_version == "2.1.0"
    assert loaded.update_last_checked == 1234.0
    assert loaded.update_release_url == settings.update_release_url


def test_development_build_requires_manual_check_even_with_saved_release_offer(qtbot):
    window = MainWindow(
        config.Settings(
            update_last_status="available",
            update_latest_version="2.1.0",
        ),
        lambda _: None,
        display_version="2.0.0+abc1234",
    )
    qtbot.addWidget(window)
    checked, opened = [], []
    window.on_check_updates = lambda: checked.append(True)
    window.on_open_update = lambda: opened.append(True)
    assert window.update_status_label.text() == "尚未檢查"
    assert window.check_updates_button.text() == "立即檢查"
    window.check_updates_button.click()
    assert checked == [True] and opened == []
    window.allow_close = True
