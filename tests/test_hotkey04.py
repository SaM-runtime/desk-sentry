import ctypes

from desk_sentry import hotkey


class FakeBackend:
    def __init__(self) -> None:
        self.active: hotkey.Chord | None = None
        self.unregisters = 0

    def register(self, chord: hotkey.Chord) -> bool:
        self.active = chord
        return True

    def unregister(self) -> None:
        self.active = None
        self.unregisters += 1


class FakeApp:
    def __init__(self) -> None:
        self.filters: list[object] = []

    def installNativeEventFilter(self, event_filter: object) -> None:  # noqa: N802
        self.filters.append(event_filter)


def dispatch(app: FakeApp, registration_id: int) -> None:
    message = hotkey._Message()  # type: ignore[attr-defined]
    message.message = hotkey.WM_HOTKEY
    message.w_param = registration_id
    address = ctypes.addressof(message)
    for event_filter in app.filters:
        event_filter.nativeEventFilter(None, address)  # type: ignore[attr-defined]


def test_two_hotkey_ids_dispatch_only_their_own_callbacks() -> None:
    called: list[str] = []
    app = FakeApp()
    mirror = hotkey.GlobalHotkey(
        lambda: called.append("mirror"),
        backend=FakeBackend(),
        registration_id=hotkey.MIRROR_HOTKEY_ID,
    )
    hide = hotkey.GlobalHotkey(
        lambda: called.append("hide"),
        backend=FakeBackend(),
        registration_id=hotkey.HIDE_HOTKEY_ID,
    )
    mirror.install(app)  # type: ignore[arg-type]
    hide.install(app)  # type: ignore[arg-type]

    dispatch(app, hotkey.HIDE_HOTKEY_ID)
    dispatch(app, hotkey.MIRROR_HOTKEY_ID)

    assert called == ["hide", "mirror"]


def test_disable_unregisters_optional_hotkey_without_changing_set_empty_behavior() -> None:
    backend = FakeBackend()
    key = hotkey.GlobalHotkey(lambda: None, backend=backend)
    assert key.set("Ctrl+Alt+H")

    assert not key.set("")
    assert key.current is not None and backend.active is not None
    key.disable()

    assert key.current is None and backend.active is None
    assert backend.unregisters == 1


def test_optional_normalization_and_conflict_use_canonical_chords() -> None:
    assert hotkey.normalize_optional("  ") == ""
    assert hotkey.normalize_optional("alt+ctrl+h") == "Ctrl+Alt+H"
    assert hotkey.normalize_optional("invalid") == hotkey.DEFAULT_HIDE_HOTKEY
    assert hotkey.conflicts("alt+ctrl+h", "CTRL+ALT+H")
    assert not hotkey.conflicts("", "Ctrl+Alt+H")
    assert not hotkey.conflicts("invalid", "Ctrl+Alt+H")
