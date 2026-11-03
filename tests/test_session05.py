import ctypes

from desk_sentry import schedule
from desk_sentry.schedule import SessionNotifications, effective_monitoring


class FakeBackend:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.registered: list[tuple[int, int]] = []
        self.unregistered: list[int] = []

    def register(self, hwnd: int, flags: int) -> bool:
        self.registered.append((hwnd, flags))
        return self.succeeds

    def unregister(self, hwnd: int) -> None:
        self.unregistered.append(hwnd)


class FakeApp:
    def __init__(self) -> None:
        self.filters: list[object] = []
        self.removed: list[object] = []

    def installNativeEventFilter(self, event_filter: object) -> None:  # noqa: N802
        self.filters.append(event_filter)

    def removeNativeEventFilter(self, event_filter: object) -> None:  # noqa: N802
        self.removed.append(event_filter)
        self.filters.remove(event_filter)


def dispatch(app: FakeApp, hwnd: int, message_id: int, event: int) -> None:
    message = schedule._Message()  # type: ignore[attr-defined]
    message.hwnd = hwnd
    message.message = message_id
    message.w_param = event
    address = ctypes.addressof(message)
    for event_filter in tuple(app.filters):
        event_filter.nativeEventFilter(None, address)  # type: ignore[attr-defined]


def test_effective_monitoring_reason_has_stable_precedence() -> None:
    assert effective_monitoring(True, False, True) == (True, None)
    assert effective_monitoring(False, False, True) == (False, "manual")
    assert effective_monitoring(False, True, False) == (False, "manual")
    assert effective_monitoring(True, True, False) == (False, "locked")
    assert effective_monitoring(True, False, False) == (False, "schedule")


def test_session_notifications_register_this_session_and_dispatch_matching_hwnd() -> None:
    calls: list[bool] = []
    backend = FakeBackend()
    app = FakeApp()
    notifications = SessionNotifications(calls.append, backend=backend)

    assert notifications.available
    assert notifications.install(app, 1234)  # type: ignore[arg-type]
    assert backend.registered == [(1234, schedule.NOTIFY_FOR_THIS_SESSION)]

    dispatch(app, 9999, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_LOCK)
    dispatch(app, 1234, 0x1234, schedule.WTS_SESSION_LOCK)
    dispatch(app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_LOCK)
    dispatch(app, 1234, schedule.WM_WTSSESSION_CHANGE, schedule.WTS_SESSION_UNLOCK)
    dispatch(app, 1234, schedule.WM_WTSSESSION_CHANGE, 0xF)

    assert calls == [True, False]

    notifications.close()
    assert backend.unregistered == [1234]
    assert len(app.removed) == 1


def test_failed_registration_does_not_install_filter_or_unregister() -> None:
    backend = FakeBackend(succeeds=False)
    app = FakeApp()
    notifications = SessionNotifications(lambda _locked: None, backend=backend)

    assert not notifications.install(app, 4321)  # type: ignore[arg-type]
    assert app.filters == []
    notifications.close()
    assert backend.unregistered == []


def test_missing_backend_and_zero_hwnd_are_unavailable_for_install(monkeypatch: object) -> None:
    app = FakeApp()
    monkeypatch.setattr(schedule.sys, "platform", "linux")  # type: ignore[attr-defined]
    unavailable = SessionNotifications(lambda _locked: None)

    assert not unavailable.available
    assert not unavailable.install(app, 1234)  # type: ignore[arg-type]

    available = SessionNotifications(lambda _locked: None, backend=FakeBackend())
    assert not available.install(app, 0)  # type: ignore[arg-type]
