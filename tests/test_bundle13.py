import hashlib
import zipfile
from pathlib import Path

import pytest

from test_packaging09 import _load_helper


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    helper = _load_helper()
    root = tmp_path / "project"
    for directory in ("src/desk_sentry", "tools", "models", "licenses", "packaging/licenses"):
        (root / directory).mkdir(parents=True)
    (root / "src/desk_sentry/__main__.py").write_text("print('fixture')")
    (root / "src/desk_sentry/__pycache__").mkdir()
    (root / "packaging/desk_sentry.ico").write_bytes(b"icon")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        (root / name).write_text("fixture")
    model_hash = hashlib.sha256(b"model").hexdigest()
    (root / "tools/fetch_model.py").write_text(
        "from types import SimpleNamespace\nPINNED = [SimpleNamespace(name=n, sha256="
        + repr(model_hash)
        + ") for n in ('a.onnx','b.onnx','c.onnx')]"
    )
    for name in ("a.onnx", "b.onnx", "c.onnx"):
        (root / "models" / name).write_bytes(b"model")
    archive = tmp_path / "python.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in ("python.exe", "pythonw.exe", "python312.zip", "python312._pth", "LICENSE.txt"):
            z.writestr(name, b"official fixture")
    monkeypatch.setattr(helper, "PYTHON_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())

    def install(site, root):
        site.mkdir(parents=True)
        (site / "numpy").mkdir()
        (site / "numpy/__init__.py").write_text("# fake wheel")
        (site / "tool.exe").write_bytes(b"unwanted wheel launcher")
        (site / "pip").mkdir()

    monkeypatch.setattr(helper, "_install_dependencies", install)
    return helper, root, archive


def test_bundle_layout_and_official_executables_only(inputs, tmp_path):
    helper, root, archive = inputs
    output = tmp_path / "dist/DeskSentry"
    helper.bundle(output, tmp_path / "metadata", python_zip=archive, root=root)
    assert (output / "python/python312._pth").read_text().splitlines() == [
        "python312.zip",
        ".",
        "..\\app",
        "Lib\\site-packages",
        "import site",
    ]
    for name in (
        "app/desk_sentry/__main__.py",
        "app/desk_sentry/_build_info.json",
        "python/Lib/site-packages/numpy/__init__.py",
        "licenses/Python-PSF-License.txt",
        "desk_sentry.ico",
        "THIRD_PARTY_NOTICES.md",
        "啟動 Desk Sentry.cmd",
    ):
        assert (output / name).is_file()
    assert len(list((output / "models").glob("*.onnx"))) == 3
    assert not list(output.rglob("__pycache__"))
    assert not (output / "python/Lib/site-packages/pip").exists()
    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*.exe")) == [
        "python/python.exe",
        "python/pythonw.exe",
    ]


def test_bad_python_hash_aborts_without_output(inputs, tmp_path):
    helper, root, archive = inputs
    archive.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        helper.bundle(tmp_path / "output", tmp_path / "meta", python_zip=archive, root=root)
    assert not (tmp_path / "output").exists()


def test_pip_targets_only_runtime_binary_wheels(tmp_path, monkeypatch):
    helper = _load_helper()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies=["numpy==2.5.1"]\n[project.optional-dependencies]\ndev=["pytest"]'
    )
    commands = []
    monkeypatch.setattr(helper.subprocess, "run", lambda cmd, **kwargs: commands.append(cmd))
    helper._install_dependencies(tmp_path / "site", tmp_path)
    cmd = commands[0]
    assert "--only-binary=:all:" in cmd and "numpy==2.5.1" in cmd
    assert cmd[cmd.index("--platform") + 1] == "win_amd64"
    assert cmd[cmd.index("--python-version") + 1] == "3.12"
    assert "pytest" not in cmd


def test_bundle_runtime_requirements_include_pinned_winrt_wheels(monkeypatch, tmp_path):
    helper = _load_helper()
    commands = []
    monkeypatch.setattr(helper.subprocess, "run", lambda cmd, **kwargs: commands.append(cmd))
    helper._install_dependencies(tmp_path / "site", Path(__file__).resolve().parents[1])
    cmd = commands[0]
    assert "winrt-runtime==4.1.0" in cmd
    for namespace in (
        "Media.Capture",
        "Media.Capture.Frames",
        "Media.MediaProperties",
        "Media.Devices",
        "Graphics.Imaging",
        "Storage.Streams",
        "Foundation",
        "Foundation.Collections",
        "Devices.Enumeration",
    ):
        assert f"winrt-Windows.{namespace}==4!10.0.28000.2957" in cmd
