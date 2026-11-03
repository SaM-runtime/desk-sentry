"""Global hotkeys via RegisterHotKey.

Event-driven only: Windows posts WM_HOTKEY to the GUI thread and a Qt native
event filter picks it up. No polling, no keyboard hooks, nothing else is read.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

DEFAULT_HOTKEY = "Ctrl+Alt+M"
DEFAULT_HIDE_HOTKEY = "Ctrl+Alt+H"
WM_HOTKEY = 0x0312
MIRROR_HOTKEY_ID = 0x5E57
HIDE_HOTKEY_ID = 0x5E58
_MODIFIERS = {"Ctrl": 0x0002, "Alt": 0x0001, "Shift": 0x0004, "Meta": 0x0008}
_MOD_NOREPEAT = 0x4000
_ORDER = ("Ctrl", "Alt", "Shift", "Meta")
_ALIASES = {
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "win": "Meta",
}


@dataclass(frozen=True, slots=True)
class Chord:
    modifiers: int
    virtual_key: int
    text: str


def parse(text: str) -> Chord | None:
    """Parse "Ctrl+Alt+M" style text. Needs ≥1 modifier and one key (A-Z, 0-9, F1-F24)."""

    parts = [p.strip() for p in text.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods: set[str] = set()
    for part in parts[:-1]:
        name = _ALIASES.get(part.lower())
        if name is None or name in mods:
            return None
        mods.add(name)
    key = parts[-1].upper()
    if len(key) == 1 and (key.isascii() and (key.isalpha() or key.isdigit())):
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    ordered = [m for m in _ORDER if m in mods]
    return Chord(
        sum(_MODIFIERS[m] for m in ordered),
        vk,
        "+".join([*ordered, key]),
    )


def normalize(text: str) -> str:
    chord = parse(text)
    return chord.text if chord else DEFAULT_HOTKEY


def normalize_optional(text: str) -> str:
    """Normalize the optional hide shortcut; blank disables it."""

    if not text.strip():
        return ""
    chord = parse(text)
    return chord.text if chord else DEFAULT_HIDE_HOTKEY


def conflicts(text: str, other: str) -> bool:
    """Whether two valid, enabled shortcut strings describe the same chord."""

    chord = parse(text)
    other_chord = parse(other)
    return chord is not None and other_chord is not None and chord == other_chord


class Backend(Protocol):
    def register(self, chord: Chord) -> bool: ...
    def unregister(self) -> None: ...


class Win32Backend:
    def __init__(self, registration_id: int = MIRROR_HOTKEY_ID) -> None:
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        user32.RegisterHotKey.restype = ctypes.c_bool
        user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.UnregisterHotKey.restype = ctypes.c_bool
        self._user32 = user32
        self._registration_id = registration_id

    def register(self, chord: Chord) -> bool:
        return bool(
            self._user32.RegisterHotKey(
                None,
                self._registration_id,
                chord.modifiers | _MOD_NOREPEAT,
                chord.virtual_key,
            )
        )

    def unregister(self) -> None:
        self._user32.UnregisterHotKey(None, self._registration_id)


class _Point(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class _Message(ctypes.Structure):
    _fields_ = (
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("w_param", ctypes.c_size_t),
        ("l_param", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("point", _Point),
        ("private", ctypes.c_uint),
    )


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey: Callable[[], None], registration_id: int) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey
        self._registration_id = registration_id

    def nativeEventFilter(self, event_type: object, message: int) -> tuple[bool, int]:  # noqa: N802
        try:
            address = int(message)
        except (TypeError, ValueError):
            return False, 0
        if not address:
            return False, 0
        msg = _Message.from_address(address)
        if msg.message != WM_HOTKEY or int(msg.w_param) != self._registration_id:
            return False, 0
        self._on_hotkey()
        return True, 0


class GlobalHotkey:
    """Owns one registration id; keeps the previous chord if a new one fails."""

    def __init__(
        self,
        on_hotkey: Callable[[], None],
        backend: Backend | None = None,
        *,
        registration_id: int = MIRROR_HOTKEY_ID,
    ) -> None:
        self._on_hotkey = on_hotkey
        self._registration_id = registration_id
        self._backend: Backend | None = backend
        if self._backend is None and sys.platform == "win32":
            try:
                self._backend = Win32Backend(registration_id)
            except (OSError, AttributeError):
                self._backend = None
        self._filter: _Filter | None = None
        self.current: Chord | None = None

    @property
    def available(self) -> bool:
        return self._backend is not None

    def install(self, app: QCoreApplication) -> None:
        if self._backend is not None and self._filter is None:
            self._filter = _Filter(self._on_hotkey, self._registration_id)
            app.installNativeEventFilter(self._filter)

    def set(self, text: str) -> bool:
        """Register ``text``. On failure the previous working chord stays active."""

        chord = parse(text)
        if chord is None or self._backend is None:
            return False
        if self.current == chord:
            return True
        previous = self.current
        if previous is not None:
            self._backend.unregister()
        if self._backend.register(chord):
            self.current = chord
            return True
        if previous is not None and not self._backend.register(previous):
            self.current = None
        return False

    def close(self) -> None:
        self.disable()

    def disable(self) -> None:
        """Unregister an optional hotkey; unlike ``set('')``, this changes state."""

        if self._backend is not None and self.current is not None:
            self._backend.unregister()
        self.current = None
