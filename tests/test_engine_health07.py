"""Engine and camera recovery retain the live stream after a one-frame memory error."""

from desk_sentry.camera import Camera
from desk_sentry.health import HealthState
from desk_sentry.sentry import Status
from tests_support import FakeCamera, ScriptedDetector, make


def test_inference_memory_error_skips_one_frame_without_reopening_camera():
    class Detector(ScriptedDetector):
        def __init__(self):
            super().__init__(enter_after=99)
            self.first = True

        def detect(self, frame, score_threshold=0.4):
            if self.first:
                self.first = False
                raise MemoryError("injected one-frame allocation failure")
            return super().detect(frame, score_threshold)

    engine, rec = make(Detector())
    engine.start()
    try:
        failed = rec.wait_for(lambda snap: snap.status is Status.ERROR)
        assert failed.health_state is HealthState.DEGRADED
        recovered = rec.wait_for(lambda snap: snap.status is Status.CLEAR)
        assert recovered.health_state is HealthState.OK
        assert len(FakeCamera.instances) == 1 and not FakeCamera.instances[0].stopped
    finally:
        engine.stop()
    assert engine.error_count == 1


def test_camera_failed_opens_wait_two_five_ten_thirty_without_real_device():
    waits = []

    def wait(delay):
        waits.append(delay)
        return len(waits) == 4

    camera = Camera(0, capture_factory=lambda: None, retry_wait=wait)
    camera.start()
    camera._thread.join(timeout=1)
    assert not camera._thread.is_alive()
    assert waits == [2.0, 5.0, 10.0, 30.0]
    camera.stop()
