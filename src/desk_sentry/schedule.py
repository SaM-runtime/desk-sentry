"""Monitoring schedule decisions and Windows session-change notifications."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable, Iterable
from ctypes import wintypes
from datetime import datetime, time, timedelta
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0


def _clock(value: str) -> time:
    if len(value) != 5 or value[2] != ":":
        raise ValueError(f"Expected HH:MM, got {value!r}")
    parsed = time.fromisoformat(value)
    if parsed.second or parsed.microsecond:
        raise ValueError(f"Expected HH:MM, got {value!r}")
    return parsed


def in_window(now: datetime, start: str, end: str, days: Iterable[int]) -> bool:
    """Return whether ``now`` belongs to a selected, start-anchored window."""

    start_time = _clock(start)
    end_time = _clock(end)
    selected = frozenset(days)
    if not selected or start_time == end_time:
        return False

    current_time = now.time().replace(tzinfo=None)
    if start_time < end_time:
        return now.weekday() in selected and start_time <= current_time < end_time
    if current_time >= start_time:
        return now.weekday() in selected
    if current_time < end_time:
        return (now.weekday() - 1) % 7 in selected
    return False


def next_boundary(
    now: datetime,
    start: str,
    end: str,
    days: Iterable[int],
) -> datetime | None:
    """Return the next strictly-future start or end boundary."""

    start_time = _clock(start)
    end_time = _clock(end)
    selected = frozenset(days)
    if not selected or start_time == end_time:
        return None

    candidates: list[datetime] = []
    for offset in range(-1, 9):
        anchor_date = now.date() + timedelta(days=offset)
        if anchor_date.weekday() not in selected:
            continue
        start_at = datetime.combine(anchor_date, start_time, tzinfo=now.tzinfo)
        end_date = anchor_date if start_time < end_time else anchor_date + timedelta(days=1)
        end_at = datetime.combine(end_date, end_time, tzinfo=now.tzinfo)
        if start_at > now:
            candidates.append(start_at)
        if end_at > now:
            candidates.append(end_at)
    return min(candidates, default=None)


def effective_monitoring(
    armed: bool,
    locked: bool,
    in_schedule: bool,
) -> tuple[bool, str | None]:
    """Resolve effective monitoring and the first applicable pause reason."""

    if not armed:
        return False, "manual"
    if locked:
        return False, "locked"
    if not in_schedule:
        return False, "schedule"
    return True, None


class SessionBackend(Protocol):
    def register(self, hwnd: int, flags: int) -> bool: ...

    def unregister(self, hwnd: int) -> None: ...


class Win32SessionBackend:
    """Small ctypes boundary around WTS session-notification registration."""

    def __init__(self) -> None:
        wtsapi32 = ctypes.windll.wtsapi32
        wtsapi32.WTSRegisterSessionNotification.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
        wtsapi32.WTSUnRegisterSessionNotification.argtypes = (ctypes.c_void_p,)
        wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
        self._wtsapi32 = wtsapi32

    def register(self, hwnd: int, flags: int) -> bool:
        return bool(self._wtsapi32.WTSRegisterSessionNotification(hwnd, flags))

    def unregister(self, hwnd: int) -> None:
        self._wtsapi32.WTSUnRegisterSessionNotification(hwnd)


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


class _SessionFilter(QAbstractNativeEventFilter):
    def __init__(self, hwnd: int, on_locked: Callable[[bool], None]) -> None:
        super().__init__()
        self._hwnd = hwnd
        self._on_locked = on_locked

    def nativeEventFilter(self, event_type: object, message: int) -> tuple[bool, int]:  # noqa: N802
        try:
            address = int(message)
        except (TypeError, ValueError):
            return False, 0
        if not address:
            return False, 0
        msg = _Message.from_address(address)
        if int(msg.hwnd or 0) != self._hwnd or msg.message != WM_WTSSESSION_CHANGE:
            return False, 0
        event = int(msg.w_param)
        if event == WTS_SESSION_LOCK:
            self._on_locked(True)
            return True, 0
        if event == WTS_SESSION_UNLOCK:
            self._on_locked(False)
            return True, 0
        return False, 0


class SessionNotifications:
    """Register one native window for event-driven lock and unlock changes."""

    def __init__(
        self,
        on_locked: Callable[[bool], None],
        backend: SessionBackend | None = None,
    ) -> None:
        self._on_locked = on_locked
        self._backend = backend
        if self._backend is None and sys.platform == "win32":
            try:
                self._backend = Win32SessionBackend()
            except (AttributeError, OSError):
                self._backend = None
        self._app: QCoreApplication | None = None
        self._filter: _SessionFilter | None = None
        self._hwnd: int | None = None

    @property
    def available(self) -> bool:
        return self._backend is not None

    def install(self, app: QCoreApplication, hwnd: int) -> bool:
        native_hwnd = int(hwnd)
        if self._backend is None or native_hwnd == 0:
            return False
        if self._filter is not None:
            return self._hwnd == native_hwnd
        if not self._backend.register(native_hwnd, NOTIFY_FOR_THIS_SESSION):
            return False
        event_filter = _SessionFilter(native_hwnd, self._on_locked)
        try:
            app.installNativeEventFilter(event_filter)
        except Exception:
            self._backend.unregister(native_hwnd)
            raise
        self._app = app
        self._filter = event_filter
        self._hwnd = native_hwnd
        return True

    def close(self) -> None:
        if self._backend is not None and self._hwnd is not None:
            self._backend.unregister(self._hwnd)
        if self._app is not None and self._filter is not None:
            self._app.removeNativeEventFilter(self._filter)
        self._app = None
        self._filter = None
        self._hwnd = None
