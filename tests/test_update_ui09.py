"""The optional checker integrates with existing settings and quiet tray surfaces."""

import io
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from desk_sentry import __version__, config
from desk_sentry import app as app_module
from desk_sentry.build_info import BuildInfo
from test_app_review05c import local_app as local_app


@pytest.mark.parametrize("discreet", [False, True])
def test_update_opt_in_badge_and_ignore_preserve_quiet_presentation(
    local_app, qtbot, monkeypatch, discreet
):
    monkeypatch.setattr(
        app_module,
        "get_build_info",
        lambda: BuildInfo(__version__, "abc1234", False, "2026-10-20T00:00:00Z", True),
    )
    request = Mock(
        side_effect=lambda *_args, **_kwargs: io.BytesIO(
            json.dumps({"tag_name": "v2.1.0", "draft": False, "prerelease": False}).encode()
        )
    )
    monkeypatch.setattr("urllib.request.urlopen", request)
    build, path, _ = local_app
    config.save(replace(config.load(path), discreet=discreet), path)
    instance = build()
    assert not instance.window.update_check.isChecked()
    assert instance.window.check_updates_button.isEnabled()
    assert __version__ in instance.window.version_label.text()
    qtbot.wait(20)
    request.assert_not_called()

    instance.window.update_check.setChecked(True)
    qtbot.waitUntil(lambda: instance._update_action.isVisible(), timeout=3000)
    assert request.call_count == 1
    assert instance._update_action.text() == ("有可用更新…" if discreet else "下載新版 2.1.0…")
    assert instance.tray.messages == []
    assert config.load(path).update_last_checked > 0
    instance._skip_update_action.trigger()
    assert not instance._update_action.isVisible()
    assert config.load(path).update_ignored_version == "2.1.0"
    instance.window.update_check.setChecked(False)
    instance.check_updates(manual=True)
    qtbot.waitUntil(lambda: not instance._update_busy, timeout=3000)
    assert request.call_count == 2
    assert not instance.window.update_check.isChecked()


def test_release_open_is_explicit_and_uses_the_offered_release(local_app, qtbot, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "get_build_info",
        lambda: BuildInfo(__version__, "abc1234", False, "2026-10-20T00:00:00Z", True),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b'{"tag_name":"v2.1.0"}'),
    )
    opened = []
    monkeypatch.setattr(
        app_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    build, _, _ = local_app
    instance = build()
    instance.window.update_check.setChecked(True)
    qtbot.waitUntil(lambda: instance._update_action.isVisible(), timeout=3000)
    assert not opened
    instance._update_action.trigger()
    assert opened and opened[0].endswith("/releases/tag/v2.1.0")
