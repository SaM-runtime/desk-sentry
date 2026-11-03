"""Task 04b window enumeration and recorded maximize-state regressions."""

from types import SimpleNamespace

from desk_sentry import actions
from desk_sentry.config import Settings
from desk_sentry.ui.main_window import MainWindow


class FakeUser32:
    def __init__(self, classes):
        self.classes = classes

    def EnumWindows(self, callback, _param):  # noqa: N802
        for hwnd in self.classes:
            callback(hwnd, 0)

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return True

    def GetWindow(self, hwnd, flag):  # noqa: N802
        return 0

    def GetWindowLongW(self, hwnd, flag):  # noqa: N802
        return 0

    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return len(f"Window {hwnd}")

    def GetWindowTextW(self, hwnd, buffer, length):  # noqa: N802
        buffer.value = f"Window {hwnd}"

    def GetWindowThreadProcessId(self, hwnd, pid):  # noqa: N802
        pid._obj.value = 1000 + hwnd

    def GetClassNameW(self, hwnd, buffer, length):  # noqa: N802
        buffer.value = self.classes[hwnd]
        return len(buffer.value)


class FakeKernel32:
    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return pid

    def QueryFullProcessImageNameW(self, handle, flags, buffer, size):  # noqa: N802
        buffer.value = r"C:\Windows\explorer.exe"
        return True

    def CloseHandle(self, handle):  # noqa: N802
        pass


def test_explorer_folder_windows_are_listed_but_shell_surfaces_are_excluded(monkeypatch, qtbot):
    classes = {1: "CabinetWClass", 2: "Progman", 3: "WorkerW", 4: "Shell_TrayWnd", 5: "OtherShell"}
    monkeypatch.setattr(
        actions.ctypes,
        "windll",
        SimpleNamespace(
            user32=FakeUser32(classes),
            kernel32=FakeKernel32(),
            dwmapi=SimpleNamespace(DwmGetWindowAttribute=lambda *args: 0),
        ),
    )

    windows = actions.list_app_windows()

    assert windows == [actions.AppWindow(1, "Window 1", "explorer.exe")]
    window = MainWindow(
        Settings(action="minimize_app"),
        lambda _settings: None,
        list_apps=lambda: [(w.exe, w.title) for w in windows],
    )
    qtbot.addWidget(window)
    assert window.target_app.findData("explorer.exe") > 0


def test_alert_restore_preserves_each_windows_pre_minimize_maximize_state(monkeypatch):
    iconic = set()
    maximized = {1}
    restored = []
    monkeypatch.setattr(
        actions.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(IsZoomed=lambda hwnd: hwnd in maximized)),
    )

    def minimize(hwnd):
        iconic.add(hwnd)
        maximized.discard(hwnd)  # IsZoomed after minimization cannot recover the old state.

    def restore(hwnd, was_maximized=False):
        restored.append((hwnd, was_maximized))
        iconic.discard(hwnd)

    minimized = actions.minimize_apps(
        ("mail.exe",),
        windows=lambda: [
            actions.AppWindow(1, "Mail", "mail.exe"),
            actions.AppWindow(2, "Draft", "mail.exe"),
        ],
        minimize=minimize,
        is_iconic=lambda hwnd: hwnd in iconic,
    )
    plan = actions.RestorePlan()
    plan.record(minimized)
    assert not plan.update("clear", 1.0)
    assert (
        actions.restore_windows(
            plan.update("clear", 6.0),
            exists=lambda hwnd: True,
            is_iconic=lambda hwnd: hwnd in iconic,
            restore=restore,
        )
        == 2
    )
    assert restored == [(1, True), (2, False)]


def test_restore_uses_maximized_or_no_activate_normal_show_commands(monkeypatch):
    iconic = {1, 2, 3}
    commands = []

    def show(hwnd, command):
        commands.append((hwnd, command))
        iconic.discard(hwnd)

    monkeypatch.setattr(
        actions.ctypes,
        "windll",
        SimpleNamespace(
            user32=SimpleNamespace(
                IsWindow=lambda hwnd: hwnd in {1, 2},
                IsIconic=lambda hwnd: hwnd in iconic,
                ShowWindow=show,
            )
        ),
    )

    assert actions.restore_windows({1: True, 2: False, 3: True}) == 2
    assert commands == [(1, 3), (2, 4)]  # SW_SHOWMAXIMIZED / SW_SHOWNOACTIVATE
