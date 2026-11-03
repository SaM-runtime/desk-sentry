"""Cue reconfiguration and shutdown preserve shared visibility deadlines."""

import pytest

from desk_sentry.sentry import Status
from desk_sentry.ui.discreet import DiscreetCue
from test_app_review05c import local_app as local_app
from test_cue_screens10 import Screen
from test_discreet_cue06 import _Timer


@pytest.mark.parametrize("kind", ["dot", "line"])
def test_new_screens_show_during_linger_and_hide_at_original_deadline(qapp, kind):
    screens = [Screen(1), Screen(2)]
    now, timer = [0.0], _Timer()
    cue = DiscreetCue(
        clock=lambda: now[0],
        timer=timer,
        screens_provider=lambda: screens,
        screen_provider=lambda: screens[0],
    )
    try:
        cue.configure(kind, "top-right", "primary")
        cue.update_status(Status.ALERT)
        cue.update_status(Status.CLEAR)
        assert len(cue.windows) == 1
        assert cue._hide_deadline == 2.0
        assert timer.started == [2000]

        now[0] = 1.2
        cue.configure(kind, "top-right", "all")
        cue.update_status(Status.CLEAR)
        assert len(cue.windows) == 2
        assert all(window.isVisible() for window in cue.windows)
        assert cue._hide_deadline == 2.0
        assert timer.started == [2000]

        now[0] = 1.999
        timer.fire()
        assert all(window.isVisible() for window in cue.windows)
        now[0] = 2.0
        timer.fire()
        assert not cue.visible
        assert all(not window.isVisible() for window in cue.windows)
    finally:
        cue.close()


@pytest.mark.parametrize("identify", [False, True])
def test_hide_cancels_preview_panels_and_timers_immediately(qapp, identify):
    screens = [Screen(1), Screen(2)]
    cue = DiscreetCue(screens_provider=lambda: screens, screen_provider=lambda: screens[0])
    try:
        cue.update_status(Status.ALERT)
        cue.update_status(Status.CLEAR)
        cue.pulse_feedback()
        cue.preview(3000 if identify else 1500, identify=identify)
        overlays = [*cue.windows, *cue._preview_windows, *cue.identifiers]
        assert all(window.isVisible() for window in overlays)
        assert cue._preview_timer.isActive()
        assert cue._fade_timer.isActive() is identify

        cue.hide()

        assert all(not window.isVisible() for window in overlays)
        assert not cue.visible
        assert not cue._preview_windows and not cue.identifiers
        assert cue._preview_deadline is None
        for timer in (cue._timer, cue._feedback_timer, cue._preview_timer, cue._fade_timer):
            assert not timer.isActive()
            # A timeout already queued before hide must not revive any overlay.
            timer.timeout.emit()
        assert not cue.visible
        assert not cue._preview_windows and not cue.identifiers
    finally:
        cue.close()


@pytest.mark.parametrize("identify", [False, True])
def test_disabling_discreet_mode_cancels_active_preview(local_app, identify):
    build, _path, _started = local_app
    app = build()
    app.window._update(discreet=True)
    app.preview_cue(identify=identify)
    cue = app.discreet_cue
    overlays = [*cue._preview_windows, *cue.identifiers]
    assert overlays and all(window.isVisible() for window in overlays)
    assert cue._preview_timer.isActive()

    app.window._update(discreet=False)

    assert all(not window.isVisible() for window in overlays)
    assert not cue._preview_windows and not cue.identifiers
    assert cue._preview_deadline is None
    assert not cue._preview_timer.isActive()
    assert not cue._fade_timer.isActive()
    cue._fade_timer.timeout.emit()
    cue._preview_timer.timeout.emit()
    assert not cue._preview_windows and not cue.identifiers
