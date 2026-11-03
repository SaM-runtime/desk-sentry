"""Windows installer mutex and graceful session termination (no camera work)."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

from desk_sentry.schedule import _Message

MUTEX_NAME = "DeskSentry.SingleInstance"
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016


class SingleInstanceMutex:
    """Keep the named object alive until shutdown so Inno can detect this app."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._kernel = None
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            self._kernel = kernel

    def acquire(self) -> bool:
        if self._kernel is None:
            return True  # QLocalServer still handles duplicate launches on other platforms.
        handle = self._kernel.CreateMutexW(None, False, self._name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS: another instance owns this lifetime.
            self._kernel.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._kernel is not None and self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class EndSessionFilter(QAbstractNativeEventFilter):
    """Finish state synchronously before returning a confirmed session end."""

    def __init__(
        self, hwnd: int, on_quit: Callable[[], None], *, shutdown_now: Callable[[], None]
    ) -> None:
        super().__init__()
        self._hwnd = hwnd
        self._on_quit = on_quit
        self._shutdown_now = shutdown_now
        self._quitting = False

    def nativeEventFilter(self, event_type: object, message: int) -> tuple[bool, int]:  # noqa: N802
        try:
            address = int(message)
        except (TypeError, ValueError):
            return False, 0
        if not address:
            return False, 0
        msg = _Message.from_address(address)
        if int(msg.hwnd or 0) != self._hwnd:
            return False, 0
        if msg.message == WM_QUERYENDSESSION:
            # Another application can still cancel this session shutdown.
            return True, 1
        if msg.message == WM_ENDSESSION and bool(msg.w_param):
            if not self._quitting:
                self._quitting = True
                self._shutdown_now()
                self._on_quit()
            return True, 0
        return False, 0
