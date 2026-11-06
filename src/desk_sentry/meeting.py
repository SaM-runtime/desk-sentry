"""Detect other applications using camera or microphone without exposing identities."""

from __future__ import annotations

import contextlib
import ntpath
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

CAPABILITIES = ("microphone", "webcam")


@dataclass(frozen=True, slots=True)
class UsageEntry:
    """One ConsentStore usage record; the identity never leaves this module."""

    identity: str
    start: int
    stop: int


class RegistryReader(Protocol):
    def entries(self, capability: str) -> Iterable[UsageEntry]: ...


class WinregApi(Protocol):
    HKEY_CURRENT_USER: object

    def OpenKey(  # noqa: N802
        self, root: object, path: str
    ) -> contextlib.AbstractContextManager[object]: ...

    def EnumKey(self, key: object, index: int) -> str: ...  # noqa: N802

    def QueryValueEx(self, key: object, name: str) -> tuple[object, int]: ...  # noqa: N802


class WinRegistryReader:
    """Read packaged and NonPackaged ConsentStore records recursively."""

    ROOT = (
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
        r"\ConsentStore"
    )

    def __init__(self, winreg_module: WinregApi | None = None) -> None:
        if winreg_module is None:
            import winreg

            self._winreg = cast(WinregApi, winreg)
        else:
            self._winreg = winreg_module

    def entries(self, capability: str) -> Iterable[UsageEntry]:
        if capability not in CAPABILITIES:
            raise ValueError(f"Unsupported capability: {capability}")
        path = rf"{self.ROOT}\{capability}"
        try:
            root_key = self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            return
        with root_key as key:
            yield from self._walk(key, path, ())

    def _walk(self, key: object, path: str, relative: tuple[str, ...]) -> Iterable[UsageEntry]:
        try:
            start = self._registry_integer(key, "LastUsedTimeStart")
            stop = self._registry_integer(key, "LastUsedTimeStop")
        except FileNotFoundError:
            pass
        else:
            yield UsageEntry(self._identity(relative), start, stop)

        index = 0
        while True:
            try:
                name = self._winreg.EnumKey(key, index)
            except OSError as exc:
                # Only ERROR_NO_MORE_ITEMS ends enumeration. Access and race
                # errors must invalidate the whole scan instead of looking clear.
                if getattr(exc, "winerror", None) != 259:
                    raise
                return
            child_path = rf"{path}\{name}"
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, child_path) as child:
                yield from self._walk(child, child_path, (*relative, name))
            index += 1

    @staticmethod
    def _identity(relative: tuple[str, ...]) -> str:
        if relative and relative[0].casefold() == "nonpackaged":
            return "\\".join(relative[1:]).replace("#", "\\")
        return "\\".join(relative)

    def _registry_integer(self, key: object, name: str) -> int:
        value = self._winreg.QueryValueEx(key, name)[0]
        if type(value) is not int:
            raise ValueError(f"ConsentStore value is not an integer: {name}")
        return value


def _normalized_executable(value: str | Path) -> str:
    text = str(value).strip().strip('"').replace("#", "\\").replace("/", "\\")
    return ntpath.normcase(ntpath.normpath(text))


def camera_in_use(reader: RegistryReader, *, self_executable: Path | str = sys.executable) -> bool:
    """Return whether a different app has an active camera or microphone record."""

    own = _normalized_executable(self_executable)
    for capability in CAPABILITIES:
        for entry in reader.entries(capability):
            if entry.start == 0 or entry.stop != 0:
                continue
            if _normalized_executable(entry.identity) == own:
                continue
            return True
    return False


class MeetingState:
    """Pure immediate-enter, delayed-exit meeting state machine."""

    def __init__(self, *, clear_delay: float = 10.0) -> None:
        if clear_delay < 0:
            raise ValueError("clear_delay must not be negative")
        self.clear_delay = clear_delay
        self.active = False
        self._clear_since: float | None = None

    def update(self, in_use: bool, now: float) -> bool | None:
        if in_use:
            self._clear_since = None
            if not self.active:
                self.active = True
                return True
            return None
        if not self.active:
            self._clear_since = None
            return None
        if self._clear_since is None:
            self._clear_since = now
            return None
        if now - self._clear_since < self.clear_delay:
            return None
        self.active = False
        self._clear_since = None
        return False

    def scan_failed(self) -> None:
        """Require a fresh full clear interval after an uncertain scan."""

        self._clear_since = None


class MeetingMonitor:
    """Poll ConsentStore on a background thread and report debounced transitions."""

    def __init__(
        self,
        callback: Callable[[bool], None],
        *,
        reader: RegistryReader | None = None,
        self_executable: Path | str | None = None,
        interval: float = 2.0,
        clear_delay: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._callback = callback
        self._reader = reader or WinRegistryReader()
        self._self_executable = self_executable or sys.executable
        self._interval = interval
        self._clock = clock
        self._state = MeetingState(clear_delay=clear_delay)
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._state.active

    def poll_once(self) -> bool | None:
        """Poll once; registry failures preserve the current state."""

        try:
            in_use = camera_in_use(self._reader, self_executable=self._self_executable)
        except Exception:  # noqa: BLE001 - an unavailable registry must not resume capture
            self._state.scan_failed()
            return None
        transition = self._state.update(in_use, self._clock())
        if transition is not None:
            self._callback(transition)
        return transition

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="meeting-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                self.poll_once()
            if self._wait(self._interval):
                return
