"""Minimize-all uses bounded Win32 calls and stores only confirmed changes."""

from types import SimpleNamespace

from desk_sentry import actions


class TimeoutMinimize:
    def __init__(self, iconic):
        self.iconic = iconic
        self.calls = []

    def __call__(self, hwnd, message, command, parameter, flags, timeout, result):
        self.calls.append((hwnd, message, command, parameter, flags, timeout))
        if hwnd == 1:  # timeout / access failure: no state change
            return 0
        if hwnd == 2:
            self.iconic.add(hwnd)
        return 1  # hwnd 3 accepts the request but never becomes minimized


class LocalUser32:
    def __init__(self):
        self.iconic = {4}
        self.SendMessageTimeoutW = TimeoutMinimize(self.iconic)
        self.restored = []

    def ShowWindow(self, hwnd, command):  # noqa: N802
        assert command != 6, "synchronous minimization can block the GUI on a hung window"
        self.restored.append((hwnd, command))
        self.iconic.discard(hwnd)
        return 1

    def IsIconic(self, hwnd):  # noqa: N802
        return hwnd in self.iconic

    def IsZoomed(self, hwnd):  # noqa: N802
        return hwnd == 2

    def IsWindow(self, hwnd):  # noqa: N802
        return True


def windows():
    return [actions.AppWindow(hwnd, f"App {hwnd}", "mail.exe") for hwnd in (1, 2, 3, 4)]


def test_minimize_all_timeout_skips_hung_and_unconfirmed_windows(monkeypatch):
    user32 = LocalUser32()
    monkeypatch.setattr(actions.ctypes, "windll", SimpleNamespace(user32=user32))

    changed = actions.minimize_all_windows(windows=windows)

    assert changed == {2: True}
    calls = user32.SendMessageTimeoutW.calls
    assert [call[0] for call in calls] == [1, 2, 3]  # continues after the failed hwnd
    for _hwnd, message, command, parameter, flags, timeout in calls:
        assert message == 0x0112 and command == 0xF020 and parameter == 0
        assert flags & 0x0002  # SMTO_ABORTIFHUNG
        assert not flags & 0x0008  # no SMTO_NOTIMEOUTIFNOTHUNG bypass
        assert 0 < timeout <= 200
    assert user32.restored == []


def test_empty_target_hide_toggle_restores_only_confirmed_minimized_windows(monkeypatch):
    user32 = LocalUser32()
    monkeypatch.setattr(actions.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(actions, "_list_minimizable_windows", windows)
    toggle = actions.HideToggle()

    assert toggle.toggle(())
    assert toggle.stored == {2: True}
    assert toggle.toggle(())
    assert toggle.stored == {}
    assert user32.restored == [(2, 3)]  # preserve the confirmed window's maximize state
    assert user32.iconic == {4}  # previously minimized window was never ours to restore
