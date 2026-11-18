"""TASK16r2: repeated explicit camera selection must not reopen the reader."""

from desk_sentry.camera_shared import CameraInfo
from desk_sentry.config import Settings
from desk_sentry.engine import Engine, _LoopState
from desk_sentry.ui.main_window import MainWindow
from test_engine16 import CameraStub, Detector


def test_legacy_selection_persists_once_and_identical_activation_does_not_reopen(qtbot):
    initial = Settings(camera_index=2, exclude_self=False)
    created = []

    def camera(index):
        reader = CameraStub(index, frame=True)
        created.append(reader)
        return reader

    engine = Engine(Detector(), initial, lambda _snapshot: None, camera_factory=camera)
    saved = []

    def save(settings):
        saved.append(settings)
        engine.apply(settings)

    window = MainWindow(
        initial,
        save,
        list_cameras=lambda: [CameraInfo(2, "USB Camera", "device-a")],
    )
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: window.camera.isEnabled() and window.camera.currentData() is not None)
    state = _LoopState()
    try:
        assert saved == []
        engine._step(state)
        assert len(created) == 1
        window.camera.activated.emit(window.camera.currentIndex())
        assert len(saved) == 1
        assert (saved[0].camera_index, saved[0].camera_id, saved[0].camera_name) == (
            2,
            "device-a",
            "USB Camera",
        )
        engine._step(state)
        assert len(created) == 2  # The newly stored identity differs from the legacy settings.
        selected_reader = created[-1]
        window.camera.activated.emit(window.camera.currentIndex())
        engine._step(state)
        assert len(saved) == 1
        assert len(created) == 2 and engine._camera is selected_reader
    finally:
        engine.stop()
