"""Optional Windows side effects when an alert starts."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath

_SW_MINIMIZE = 6
_SW_SHOWNOACTIVATE = 4
_SW_SHOWMAXIMIZED = 3
_SW_RESTORE = 9
_WM_SYSCOMMAND = 0x0112
_SC_MINIMIZE = 0xF020
_SMTO_ABORTIFHUNG = 0x0002
_MINIMIZE_TIMEOUT_MS = 200
_GW_OWNER = 4
_GW_HWNDNEXT = 2
_MAX_Z_ORDER_WINDOWS = 4096
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DWMWA_CLOAKED = 14
RESTORE_QUIET_SECONDS = 5.0
RECENT_INPUT_SECONDS = 60.0
EVADE_DWELL_SECONDS = 0.4
EVADE_COOLDOWN_SECONDS = 30.0
EVADE_NEAR_HEIGHT_MULTIPLIER = 1.15
WindowStates = dict[int, bool]  # hwnd -> maximized immediately before our minimization
# Windows' own shell surfaces that look like app windows but are not choices.
_SYSTEM_EXES = frozenset(
    {
        "explorer.exe",
        "textinputhost.exe",
        "applicationframehost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "lockapp.exe",
    }
)


@dataclass(frozen=True, slots=True)
class AppWindow:
    hwnd: int
    title: str
    exe: str  # lower-case file name, e.g. "chrome.exe"


@dataclass(frozen=True, slots=True)
class CoverResult:
    found: bool = False
    activated: bool = False
    previous: int | None = None
    target: int | None = None


def _foreground_window() -> int | None:
    if sys.platform != "win32":
        return None
    get = ctypes.windll.user32.GetForegroundWindow
    get.argtypes = ()
    get.restype = wintypes.HWND
    return int(get() or 0) or None


def _set_foreground(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    activate = ctypes.windll.user32.SetForegroundWindow
    activate.argtypes = (wintypes.HWND,)
    activate.restype = wintypes.BOOL
    return bool(activate(hwnd))


def _z_order() -> list[int]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    top, following = user32.GetTopWindow, user32.GetWindow
    top.argtypes, top.restype = (wintypes.HWND,), wintypes.HWND
    following.argtypes, following.restype = (wintypes.HWND, wintypes.UINT), wintypes.HWND
    result: list[int] = []
    seen: set[int] = set()
    hwnd = top(None)
    while hwnd and int(hwnd) not in seen and len(result) < _MAX_Z_ORDER_WINDOWS:
        seen.add(int(hwnd))
        result.append(int(hwnd))
        hwnd = following(hwnd, _GW_HWNDNEXT)
    return result


def bring_cover(
    exe: str,
    *,
    windows: Callable[[], list[AppWindow]] | None = None,
    z_order: Callable[[], list[int]] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
    foreground: Callable[[], int | None] | None = None,
    activate: Callable[[int], bool] | None = None,
) -> CoverResult:
    """Bring the topmost visible target forward, respecting Windows' activation result."""
    target = exe.strip().lower()
    if not target:
        return CoverResult()
    is_iconic = is_iconic or _is_iconic
    candidates = [
        w
        for w in (windows or _list_minimizable_windows)()
        if w.exe == target and not is_iconic(w.hwnd)
    ]
    if not candidates:
        return CoverResult()
    order = {hwnd: index for index, hwnd in enumerate((z_order or _z_order)())}
    selected = min(candidates, key=lambda w: order.get(w.hwnd, _MAX_Z_ORDER_WINDOWS))
    previous = (foreground or _foreground_window)()
    activated = (activate or _set_foreground)(selected.hwnd)
    return CoverResult(True, activated, previous if activated else None, selected.hwnd)


def restore_foreground(
    hwnd: int,
    *,
    exists: Callable[[int], bool] | None = None,
    activate: Callable[[int], bool] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
    restore: Callable[[int], object] | None = None,
) -> bool:
    if not (exists or _window_exists)(hwnd):
        return False
    if (is_iconic or _is_iconic)(hwnd):
        (restore or _restore_for_foreground)(hwnd)
    return bool((activate or _set_foreground)(hwnd))


def _restore_for_foreground(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    show = ctypes.windll.user32.ShowWindowAsync
    show.argtypes = (wintypes.HWND, ctypes.c_int)
    show.restype = wintypes.BOOL
    return bool(show(hwnd, _SW_RESTORE))


def play_alert_sound() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)


def stop_alert_sound() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(None, 0)


def _minimize_window(hwnd: int) -> object:
    if sys.platform != "win32":
        return False
    return ctypes.windll.user32.ShowWindow(hwnd, _SW_MINIMIZE)


def _minimize_window_with_timeout(hwnd: int) -> bool:
    """Bound each minimize-all request even when another app stops responding."""
    if sys.platform != "win32":
        return False
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    )
    send.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t()
    return bool(
        send(
            hwnd,
            _WM_SYSCOMMAND,
            _SC_MINIMIZE,
            0,
            _SMTO_ABORTIFHUNG,
            _MINIMIZE_TIMEOUT_MS,
            ctypes.byref(result),
        )
    )


def _restore_window(hwnd: int, was_maximized: bool) -> object:
    if sys.platform != "win32":
        return False
    # Windows has no ShowWindow command for maximizing without activation. Keep
    # the previous maximize state; ordinary windows still restore without focus.
    command = _SW_SHOWMAXIMIZED if was_maximized else _SW_SHOWNOACTIVATE
    return ctypes.windll.user32.ShowWindow(hwnd, command)


def _is_iconic(hwnd: int) -> bool:
    return sys.platform == "win32" and bool(ctypes.windll.user32.IsIconic(hwnd))


def _is_zoomed(hwnd: int) -> bool:
    return sys.platform == "win32" and bool(ctypes.windll.user32.IsZoomed(hwnd))


def _window_exists(hwnd: int) -> bool:
    return sys.platform == "win32" and bool(ctypes.windll.user32.IsWindow(hwnd))


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


def last_input_age() -> float:
    """Seconds since session input; unavailable timestamps defer automatic actions."""
    if sys.platform != "win32":
        return 0.0
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
    except (AttributeError, OSError):
        return 0.0
    return ((tick - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0


def minimize_all_windows(
    *,
    windows: Callable[[], list[AppWindow]] | None = None,
    minimize: Callable[[int], object] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
    is_zoomed: Callable[[int], bool] | None = None,
) -> WindowStates:
    """Minimize eligible app windows, recording only successful changes and their old state."""

    return _minimize_matching(
        None,
        windows=windows or _list_minimizable_windows,
        minimize=minimize or _minimize_window_with_timeout,
        is_iconic=is_iconic or _is_iconic,
        is_zoomed=is_zoomed or _is_zoomed,
    )


def lock_workstation() -> bool:
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.LockWorkStation())


def _exe_name(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return PureWindowsPath(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def list_app_windows() -> list[AppWindow]:
    """Visible, titled, top-level app windows (what the taskbar would show), not ours."""

    return _enumerate_app_windows(include_uwp=False)


def _list_minimizable_windows() -> list[AppWindow]:
    return _enumerate_app_windows(include_uwp=True)


def _enumerate_app_windows(*, include_uwp: bool) -> list[AppWindow]:

    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    own_pid = os.getpid()
    found: list[AppWindow] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, _GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        if cloaked.value:  # hidden by Windows (suspended store app, other desktop)
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        exe = _exe_name(pid.value)
        folder_window = False
        uwp_window = False
        if exe == "explorer.exe" or (include_uwp and exe == "applicationframehost.exe"):
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            folder_window = exe == "explorer.exe" and class_name.value == "CabinetWClass"
            uwp_window = (
                exe == "applicationframehost.exe" and class_name.value == "ApplicationFrameWindow"
            )
        if exe and (exe not in _SYSTEM_EXES or folder_window or uwp_window):
            found.append(AppWindow(int(hwnd), title.value, exe))
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found


def minimize_app(
    exe: str,
    *,
    windows: Callable[[], list[AppWindow]] = list_app_windows,
    minimize: Callable[[int], object] | None = None,
) -> int:
    """Minimize every window of program ``exe`` (matched by program, not title:
    browser titles change with each tab). Returns how many were minimized.

    Windows of programs running as administrator cannot be minimized from a
    normal process (Windows UIPI); those are silently skipped.
    """

    target = exe.strip().lower()
    if not target:
        return 0
    if minimize is None:
        if sys.platform != "win32":
            return 0
        user32 = ctypes.windll.user32

        def minimize(hwnd: int) -> object:
            return user32.ShowWindow(hwnd, _SW_MINIMIZE)

    count = 0
    for window in windows():
        if window.exe == target:
            minimize(window.hwnd)
            count += 1
    return count


def minimize_apps(
    exes: Iterable[str],
    *,
    windows: Callable[[], list[AppWindow]] = list_app_windows,
    minimize: Callable[[int], object] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
    is_zoomed: Callable[[int], bool] | None = None,
) -> WindowStates:
    """Minimize matching programs once, recording changed handles and their old maximize state."""

    if isinstance(exes, str):
        exes = (exes,)
    targets = {exe.strip().lower() for exe in exes if exe.strip()}
    if not targets:
        return {}
    return _minimize_matching(
        targets,
        windows=windows,
        minimize=minimize or _minimize_window,
        is_iconic=is_iconic or _is_iconic,
        is_zoomed=is_zoomed or _is_zoomed,
    )


def _minimize_matching(
    targets: set[str] | None,
    *,
    windows: Callable[[], list[AppWindow]],
    minimize: Callable[[int], object],
    is_iconic: Callable[[int], bool],
    is_zoomed: Callable[[int], bool],
) -> WindowStates:
    changed: WindowStates = {}
    for window in windows():
        if targets is not None and window.exe not in targets:
            continue
        if is_iconic(window.hwnd):
            continue
        was_maximized = is_zoomed(window.hwnd)
        minimize(window.hwnd)
        if is_iconic(window.hwnd):
            changed[window.hwnd] = was_maximized
    return changed


def restore_windows(
    hwnds: Mapping[int, bool],
    *,
    exists: Callable[[int], bool] | None = None,
    is_iconic: Callable[[int], bool] | None = None,
    restore: Callable[[int, bool], object] | None = None,
) -> int:
    """Restore existing iconic windows to their recorded normal/maximized state.

    Normal restoration does not activate; maximized restoration prioritizes size
    and Windows may activate it. No foreground activation is requested separately.
    """

    exists = exists or _window_exists
    is_iconic = is_iconic or _is_iconic
    restore = restore or _restore_window
    count = 0
    for hwnd, was_maximized in hwnds.items():
        if not exists(hwnd) or not is_iconic(hwnd):
            continue
        restore(hwnd, was_maximized)
        if not is_iconic(hwnd):
            count += 1
    return count


class RestorePlan:
    """Plan restoration after raw status stays quiet; performs no window side effects."""

    def __init__(self) -> None:
        self._hwnds: WindowStates = {}
        self._quiet_since: float | None = None

    @property
    def pending(self) -> WindowStates:
        return self._hwnds.copy()

    def record(self, hwnds: Mapping[int, bool]) -> None:
        for hwnd, was_maximized in hwnds.items():
            self._hwnds.setdefault(hwnd, was_maximized)
        self._quiet_since = None

    def update(self, raw_status: str, now: float) -> WindowStates:
        status = str(raw_status)
        if status == "away":
            self.clear()
        elif status in ("alert", "approaching"):
            self._quiet_since = None
        elif status in ("clear", "watching", "notice") and self._hwnds:
            if self._quiet_since is None:
                self._quiet_since = now
            elif now - self._quiet_since >= RESTORE_QUIET_SECONDS:
                ready = self._hwnds.copy()
                self.clear()
                return ready
        else:
            self._quiet_since = None
        return {}

    def clear(self) -> None:
        self._hwnds.clear()
        self._quiet_since = None


class EvadePlan:
    """Confirm automatic actions independently of the immediate alert presentation."""

    def __init__(self) -> None:
        self._pending_since: float | None = None
        self._last_executed: float | None = None

    def update(
        self,
        raw_status: str,
        now: float,
        *,
        alert_started: bool = False,
        eligible: bool = False,
    ) -> bool:
        if raw_status not in ("alert", "approaching"):
            self._pending_since = None
            return False
        if alert_started:
            cooling_down = (
                self._last_executed is not None
                and now < self._last_executed + EVADE_COOLDOWN_SECONDS
            )
            self._pending_since = None if cooling_down else now
        if (
            self._pending_since is None
            or now < self._pending_since + EVADE_DWELL_SECONDS
            or not eligible
        ):
            return False
        self._pending_since = None
        self._last_executed = now
        return True

    def cancel(self) -> None:
        self._pending_since = None


class ForegroundPlan:
    """Restore the foreground after the same quiet interval as minimized windows."""

    def __init__(self) -> None:
        self._previous: int | None = None
        self._quiet_since: float | None = None

    def record(self, previous: int | None) -> None:
        if self._previous is None:
            self._previous = previous
        self._quiet_since = None

    def update(self, raw_status: str, now: float) -> int | None:
        if raw_status == "away":
            self.clear()
        elif raw_status in ("alert", "approaching"):
            self._quiet_since = None
        elif raw_status in ("clear", "watching", "notice") and self._previous is not None:
            if self._quiet_since is None:
                self._quiet_since = now
            elif now >= self._quiet_since + RESTORE_QUIET_SECONDS:
                previous = self._previous
                self.clear()
                return previous
        else:
            self._quiet_since = None
        return None

    def clear(self) -> None:
        self._previous = None
        self._quiet_since = None


class AwayPlan:
    """Decide one away action using only raw status, elapsed time and input age."""

    def __init__(self) -> None:
        self._away_since: float | None = None
        self._executed = False

    def update(
        self,
        raw_status: str,
        now: float,
        *,
        action: str,
        minutes: int,
        target_apps: Iterable[str] = (),
        last_input_age: Callable[[], float],
    ) -> str | None:
        if raw_status != "away":
            self._away_since = None
            self._executed = False
            return None
        if self._away_since is None:
            self._away_since = now
        if self._executed or now - self._away_since < minutes * 60:
            return None
        if action not in ("lock", "minimize_app") or (
            action == "minimize_app" and not tuple(target_apps)
        ):
            return None
        if last_input_age() < RECENT_INPUT_SECONDS:
            self._away_since = now
            return None
        self._executed = True
        return action


class HideToggle:
    """Shortcut-only minimize/restore state, separate from alert restoration."""

    def __init__(
        self,
        *,
        minimize_selected: Callable[[Iterable[str]], WindowStates] = minimize_apps,
        minimize_all: Callable[[], WindowStates] = minimize_all_windows,
        restore: Callable[[Mapping[int, bool]], int] = restore_windows,
    ) -> None:
        self._minimize_selected = minimize_selected
        self._minimize_all = minimize_all
        self._restore = restore
        self._hwnds: WindowStates = {}

    @property
    def stored(self) -> WindowStates:
        return self._hwnds.copy()

    def toggle(self, exes: Iterable[str]) -> bool:
        if self._hwnds:
            hwnds = self._hwnds.copy()
            self._hwnds.clear()
            return self._restore(hwnds) > 0
        targets = tuple(exes)
        self._hwnds = self._minimize_selected(targets) if targets else self._minimize_all()
        return bool(self._hwnds)


def run_action(action: str, target_apps: str | Iterable[str] = ()) -> bool:
    if action == "minimize":
        return bool(minimize_all_windows())
    if action == "minimize_app":
        targets = (target_apps,) if isinstance(target_apps, str) else target_apps
        return bool(minimize_apps(targets))
    if action == "lock":
        return lock_workstation()
    return False
