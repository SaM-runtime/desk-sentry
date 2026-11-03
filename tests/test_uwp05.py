from types import SimpleNamespace

from desk_sentry import actions
from test_window_actions04b import FakeKernel32, FakeUser32


class UwpUser32(FakeUser32):
    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return 0 if hwnd == 4 else super().GetWindowTextLengthW(hwnd)


class UwpKernel32(FakeKernel32):
    def QueryFullProcessImageNameW(self, handle, flags, buffer, size):  # noqa: N802
        buffer.value = (
            r"C:\Windows\explorer.exe"
            if handle == 1001
            else (r"C:\Windows\System32\ApplicationFrameHost.exe")
        )
        return True


def test_minimize_all_includes_visible_uwp_but_picker_excludes_it(monkeypatch):
    classes = {
        1: "CabinetWClass",
        2: "ApplicationFrameWindow",
        3: "ApplicationFrameWindow",
        4: "ApplicationFrameWindow",
        5: "OtherShell",
    }

    def cloaked(hwnd, attribute, value, size):
        value._obj.value = hwnd == 3
        return 0

    monkeypatch.setattr(
        actions.ctypes,
        "windll",
        SimpleNamespace(
            user32=UwpUser32(classes),
            kernel32=UwpKernel32(),
            dwmapi=SimpleNamespace(DwmGetWindowAttribute=cloaked),
        ),
    )
    iconic = set()
    assert [w.hwnd for w in actions.list_app_windows()] == [1]
    changed = actions.minimize_all_windows(
        minimize=iconic.add,
        is_iconic=lambda hwnd: hwnd in iconic,
        is_zoomed=lambda hwnd: hwnd == 2,
    )
    assert changed == {1: False, 2: True}
