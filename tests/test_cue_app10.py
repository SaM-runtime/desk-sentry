"""Preview and tray wiring never run alert actions or add diagnostics events."""

from unittest.mock import Mock

from desk_sentry import actions
from desk_sentry.app import _diagnostics_settings
from desk_sentry.config import Settings
from test_app_review05c import local_app as local_app


def test_discreet_tray_hides_compact_and_restores_on_disable(local_app):
    build, _path, _started = local_app
    app = build()
    assert app._compact_action.isVisible()
    app.window._update(discreet=True)
    assert not app._compact_action.isVisible()
    app.window._update(discreet=False)
    assert app._compact_action.isVisible()


def test_identify_and_automatic_preview_have_no_alert_or_log_side_effects(local_app, monkeypatch):
    build, _path, _started = local_app
    app = build()
    app.window._update(discreet=True)
    log, action = Mock(), Mock()
    monkeypatch.setattr(app._diagnostics, "write", log)
    monkeypatch.setattr(actions, "run_action", action)
    app.window.identify_screens.click()
    assert app.discreet_cue.identifiers
    assert app.discreet_cue._preview_timer.interval() == 3000
    log.assert_not_called()
    action.assert_not_called()
    app.window.discreet_corner.setCurrentIndex(app.window.discreet_corner.findData("bottom-left"))
    assert app.discreet_cue._preview_timer.interval() == 1500
    assert not app.discreet_cue.identifiers
    assert all(w.geometry().bottomLeft().x() >= 0 for w in app.discreet_cue._preview_windows)
    action.assert_not_called()
    assert "discreet_screen_mode" not in _diagnostics_settings(Settings())
    assert "discreet_screens" not in _diagnostics_settings(Settings())
