"""NOTICE lingers one second and marker confirmation temporarily shows a gray dot."""

import pytest
from PySide6.QtCore import QRect

from desk_sentry.sentry import Status
from desk_sentry.ui import theme
from desk_sentry.ui.discreet import DiscreetCue
from test_discreet_cue06 import _Screen, _Timer
from test_discreet_cue06 import no_real_window as no_real_window


def cue_with_clock(now, cue="dot"):
    signal_timer, feedback_timer = _Timer(), _Timer()
    screen = _Screen(QRect(0, 0, 800, 600))
    cue = DiscreetCue(
        cue=cue,
        clock=lambda: now[0],
        timer=signal_timer,
        feedback_timer=feedback_timer,
        screen_provider=lambda: screen,
        screen_setter=lambda _window, _screen: None,
    )
    return cue, signal_timer, feedback_timer


@pytest.mark.parametrize("kind", ["dot", "line"])
def test_notice_uses_muted_color_and_hides_after_one_second(qtbot, no_real_window, kind):
    now = [0.0]
    cue, timer, _feedback = cue_with_clock(now, kind)
    cue.update_status(Status.NOTICE)
    qtbot.addWidget(cue.window)
    assert cue.visible and cue.window.color == theme.NOTICE
    cue.update_status(Status.CLEAR)
    now[0] = 0.999
    timer.fire()
    assert cue.visible
    now[0] = 1.0
    timer.fire()
    assert not cue.visible


def test_marker_gray_dot_ends_at_point_four_and_preserves_active_alert(qtbot, no_real_window):
    now = [0.0]
    cue, _timer, feedback = cue_with_clock(now, "line")
    cue.update_status(Status.ALERT)
    qtbot.addWidget(cue.window)
    cue.pulse_feedback()
    assert cue.window.color == theme.STATUS_COLOR[Status.PAUSED]
    assert cue.window.geometry() == QRect(792, 2, 6, 6)
    now[0] = 0.399
    feedback.fire()
    assert cue.window.color == theme.STATUS_COLOR[Status.PAUSED]
    now[0] = 0.4
    feedback.fire()
    assert cue.visible and cue.window.color == theme.STATUS_COLOR[Status.ALERT]
    assert cue.window.geometry() == QRect(0, 0, 800, 2)


def test_marker_only_dot_hides_and_none_keeps_no_window(qtbot, no_real_window):
    now = [0.0]
    cue, _timer, feedback = cue_with_clock(now)
    cue.pulse_feedback()
    qtbot.addWidget(cue.window)
    assert cue.visible
    now[0] = 0.4
    feedback.fire()
    assert not cue.visible
    cue.configure("none", "bottom-right")
    cue.pulse_feedback()
    assert cue.window is None and not cue.visible
