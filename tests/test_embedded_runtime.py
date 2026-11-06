"""Runtime paths for the official embedded-Python release layout."""

from pathlib import Path
from types import SimpleNamespace

from desk_sentry import actions, app, startup


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "Desk Sentry"
    package = root / "app" / "desk_sentry"
    runtime = root / "python"
    models = root / "models"
    package.mkdir(parents=True)
    runtime.mkdir()
    models.mkdir()
    (runtime / "python.exe").write_bytes(b"official fixture")
    (runtime / "pythonw.exe").write_bytes(b"official fixture")
    model = models / app.MODEL_NAME
    model.write_bytes(b"model fixture")
    return root, package, model


def test_embedded_model_path_is_derived_from_package_not_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    _, package, expected = _layout(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "models").mkdir()
    (unrelated / "models" / app.MODEL_NAME).write_bytes(b"wrong model")

    monkeypatch.setattr(app, "__file__", str(package / "app.py"))
    monkeypatch.chdir(unrelated)

    assert app.model_path() == expected


def test_embedded_startup_uses_pythonw_module_command(tmp_path: Path, monkeypatch) -> None:
    root, _, _ = _layout(tmp_path)
    monkeypatch.setattr(startup.sys, "executable", str(root / "python" / "python.exe"))

    assert startup.launch_command() == (
        f'"{root / "python" / "pythonw.exe"}" -m desk_sentry --minimized'
    )


def test_window_filter_excludes_own_embedded_pythonw_process(monkeypatch) -> None:
    """Using pythonw.exe does not make the app minimize its own windows."""

    own_pid = 1200
    monkeypatch.setattr(actions.os, "getpid", lambda: own_pid)

    class User32:
        def EnumWindows(self, callback, _param):  # noqa: N802
            callback(42, 0)

        def IsWindowVisible(self, _hwnd):  # noqa: N802
            return True

        def GetWindow(self, _hwnd, _flag):  # noqa: N802
            return 0

        def GetWindowLongW(self, _hwnd, _flag):  # noqa: N802
            return 0

        def GetWindowTextLengthW(self, _hwnd):  # noqa: N802
            return 4

        def GetWindowTextW(self, _hwnd, buffer, _length):  # noqa: N802
            buffer.value = "Self"

        def GetWindowThreadProcessId(self, _hwnd, pid):  # noqa: N802
            pid._obj.value = own_pid

    monkeypatch.setattr(
        actions.ctypes,
        "windll",
        SimpleNamespace(
            user32=User32(),
            dwmapi=SimpleNamespace(DwmGetWindowAttribute=lambda *_args: 0),
        ),
    )
    monkeypatch.setattr(actions, "_exe_name", lambda _pid: (_ for _ in ()).throw(AssertionError()))

    assert actions.list_app_windows() == []
