from dataclasses import replace

import pytest

from desk_sentry.config import Settings
from desk_sentry.diagnostics import DiagnosticsLog
from desk_sentry.engine import Engine, _LoopState
from desk_sentry.sentry import Status
from test_diagnostics import read
from test_status_integration import FrameDetector
from tests_support import FakeCamera


@pytest.mark.parametrize("reason", ["manual", "locked", "schedule"])
def test_effective_pause_releases_camera_logs_reason_and_preserves_user_armed(
    monkeypatch, tmp_path, reason
):
    user = Settings(armed=True, exclude_self=False)
    snapshots = []
    engine = Engine(FrameDetector([[]]), user, snapshots.append, camera_factory=FakeCamera)
    log = DiagnosticsLog(tmp_path)
    engine.diagnostics = log
    monkeypatch.setattr(engine._wake, "wait", lambda timeout: True)
    state = _LoopState()
    try:
        engine._step(state)
        camera = engine._camera
        assert camera is not None
        engine.apply(replace(user, armed=False), pause_reason=reason)
        engine._step(state)
        assert camera.stopped
        assert snapshots[-1].status is Status.PAUSED
        assert user.armed is True
        engine.apply(user)
        engine._step(state)
        assert engine._camera is not None and engine._camera is not camera
    finally:
        engine.stop()
    assert any(r["kind"] == "paused" and r["reason"] == reason for r in read(tmp_path))
