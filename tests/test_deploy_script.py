"""Subprocess contract tests for deployment and the versioned post-commit hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SOURCE = PROJECT_ROOT / "tools" / "deploy.ps1"
HOOK_SOURCE = PROJECT_ROOT / "tools" / "git-hooks" / "post-commit"
POWERSHELL = shutil.which("powershell")
GIT_SH = next(
    (
        candidate
        for candidate in (
            Path(r"C:\Program Files\Git\bin\sh.exe"),
            Path(r"C:\Program Files\Git\usr\bin\sh.exe"),
        )
        if candidate.is_file()
    ),
    None,
)
MODEL_NAMES = (
    "object_detection_nanodet_2022nov.onnx",
    "face_detection_yunet_2026may.onnx",
    "face_recognition_sface_2021dec.onnx",
)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=20
    )


def _run_deploy(
    repo: Path,
    install_dir: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "tools" / "deploy.ps1"),
            "-InstallDir",
            str(install_dir),
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=env,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _wait_for_file(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.025)
    return path.exists()


def _install_fake_pyinstaller(repo: Path, test_env: dict[str, str]) -> None:
    package = repo / "fake-modules" / "PyInstaller"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        """\
import pathlib
import json
import subprocess
import sys

dist = pathlib.Path(sys.argv[sys.argv.index("--distpath") + 1])
output = dist / "DeskSentry"
output.mkdir(parents=True, exist_ok=True)
(output / "DeskSentry.exe").write_bytes(b"new executable")
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True
).stdout.strip()
build_info = output / "_internal" / "desk_sentry" / "_build_info.json"
build_info.parent.mkdir(parents=True, exist_ok=True)
build_info.write_text(
    json.dumps(
        {
            "version": "2.0.0",
            "commit": head[:7],
            "dirty": False,
            "built_at": "2026-10-20T08:00:00Z",
            "release": False,
            "display_version": f"2.0.0+{head[:7]}",
        }
    ),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    test_env["PYTHONPATH"] = str(repo / "fake-modules")


@pytest.fixture
def deploy_repo(tmp_path: Path) -> Iterator[tuple[Path, Path, dict[str, str]]]:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    if not DEPLOY_SOURCE.is_file() or not HOOK_SOURCE.is_file():
        pytest.fail("Task 08 deploy script and hook must exist")
    venv_config = Path(sys.executable).parents[1] / "pyvenv.cfg"
    if not venv_config.is_file():
        pytest.skip(
            "deploy_repo requires a virtualenv interpreter with pyvenv.cfg; "
            "run .venv\\Scripts\\python.exe -m pytest (base interpreter detected)"
        )

    repo = tmp_path / "repo"
    install_dir = tmp_path / "install"
    for directory in (
        repo / "src",
        repo / "packaging",
        repo / "models",
        repo / "licenses",
        repo / "tools" / "git-hooks",
        repo / ".venv" / "Scripts",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(DEPLOY_SOURCE, repo / "tools" / "deploy.ps1")
    shutil.copy2(HOOK_SOURCE, repo / "tools" / "git-hooks" / "post-commit")
    shutil.copy2(Path(sys.executable), repo / ".venv" / "Scripts" / "python.exe")
    shutil.copy2(venv_config, repo / ".venv" / "pyvenv.cfg")
    (repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "packaging" / "DeskSentry.spec").write_text("# fixture spec\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (repo / "licenses" / "NOTICE.txt").write_text("fixture\n", encoding="utf-8")
    for model_name in MODEL_NAMES:
        (repo / "models" / model_name).write_bytes(b"fixture model")

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Desk Sentry Tests")
    _run_git(repo, "config", "user.email", "tests@users.noreply.github.com")
    _run_git(
        repo,
        "add",
        "src",
        "packaging",
        "pyproject.toml",
        "models",
        "licenses",
        "tools",
    )
    _run_git(repo, "commit", "-m", "fixture")

    test_env = os.environ.copy()
    test_env["LOCALAPPDATA"] = str(tmp_path / "localappdata")
    yield repo, install_dir, test_env


def test_dry_run_same_version_skips(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    (install_dir / "DeskSentry.exe").write_bytes(b"installed")
    head = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
    version_file = install_dir / "VERSION.txt"
    original_version = f"{head}\nold deployment time\n"
    version_file.write_text(original_version, encoding="utf-8")

    result = _run_deploy(repo, install_dir, "-DryRun", env=test_env)
    output = _output(result)

    assert result.returncode == 0, output
    assert "[version]" in output and "same HEAD" in output
    assert version_file.read_text(encoding="utf-8") == original_version
    assert not (repo / ".tmp" / "deploy").exists()
    assert not (Path(test_env["LOCALAPPDATA"]) / "DeskSentry" / "deploy.log").exists()


def test_dry_run_new_version_format_uses_full_hash_on_second_line(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    (install_dir / "DeskSentry.exe").write_bytes(b"installed")
    head = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
    (install_dir / "VERSION.txt").write_text(
        f"2.0.0+{head[:7]}\n{head}\n2026-10-20T08:00:00Z\n",
        encoding="utf-8",
    )

    result = _run_deploy(repo, install_dir, "-DryRun", env=test_env)
    output = _output(result)

    assert result.returncode == 0, output
    assert "[version]" in output and "same HEAD" in output
    assert "[build]" not in output


def test_force_dry_run_lists_every_deployment_step(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo

    result = _run_deploy(repo, install_dir, "-DryRun", "-Force", env=test_env)
    output = _output(result)

    assert result.returncode == 0, output
    positions = [
        output.index(f"[{step}]") for step in ("build", "stop", "swap", "registry", "start")
    ]
    assert positions == sorted(positions)
    assert "--quit" in output and "CloseMainWindow/force fallback" in output
    assert not install_dir.exists()
    assert not (repo / ".tmp" / "deploy").exists()


def test_tracked_dirty_source_skips_with_success(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    (repo / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = _run_deploy(repo, install_dir, "-DryRun", env=test_env)
    output = _output(result)

    assert result.returncode == 0, output
    assert "[preflight]" in output and "dirty" in output.casefold()
    assert "[build]" not in output
    assert not install_dir.exists()


def test_missing_required_model_is_an_error(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    missing_name = MODEL_NAMES[0]
    (repo / "models" / missing_name).unlink()
    _run_git(repo, "add", "-u", "models")
    _run_git(repo, "commit", "-m", "remove required model")

    result = _run_deploy(repo, install_dir, "-DryRun", env=test_env)
    output = _output(result)

    assert result.returncode != 0, output
    assert missing_name in output
    assert not install_dir.exists()


def test_build_failure_preserves_installed_version(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    (install_dir / "DeskSentry.exe").write_bytes(b"existing executable")
    (install_dir / "VERSION.txt").write_text("old-head\nold deployment time\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in install_dir.iterdir()}

    # cmd.exe is a real executable but cannot satisfy ``python -m PyInstaller``.
    shutil.copy2(Path(os.environ["COMSPEC"]), repo / ".venv" / "Scripts" / "python.exe")
    result = _run_deploy(
        repo,
        install_dir,
        "-Force",
        "-SkipRegistry",
        "-NoStart",
        env=test_env,
    )
    output = _output(result)

    assert result.returncode != 0, output
    assert "[build]" in output
    after = {path.name: path.read_bytes() for path in install_dir.iterdir()}
    assert after == before
    assert not Path(f"{install_dir}.prev").exists()


def test_second_deploy_skips_while_named_mutex_is_owned(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    ready = repo.parent / "mutex-ready.txt"
    release = repo.parent / "mutex-release.txt"
    holder_env = test_env.copy()
    holder_env["TEST_MUTEX_READY"] = str(ready)
    holder_env["TEST_MUTEX_RELEASE"] = str(release)
    holder = subprocess.Popen(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                '$m=New-Object Threading.Mutex($false,"Local\\DeskSentry.Deploy.$sid"); '
                "$null=$m.WaitOne(); Set-Content -LiteralPath $env:TEST_MUTEX_READY ready; "
                "while (-not (Test-Path -LiteralPath $env:TEST_MUTEX_RELEASE)) "
                "{ Start-Sleep -Milliseconds 25 }; $m.ReleaseMutex(); $m.Dispose()"
            ),
        ],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=holder_env,
    )
    try:
        assert _wait_for_file(ready), "mutex holder did not become ready"
        result = _run_deploy(
            repo,
            install_dir,
            "-Force",
            "-SkipRegistry",
            "-NoStart",
            env=test_env,
        )
        output = _output(result)
        assert result.returncode == 0, output
        assert "[lock]" in output and "another deploy is running" in output
        assert not install_dir.exists()
    finally:
        release.write_text("release\n", encoding="utf-8")
        holder_stdout, holder_stderr = holder.communicate(timeout=10)
        assert holder.returncode == 0, f"{holder_stdout}\n{holder_stderr}"


def test_swap_copy_failure_restores_existing_installation(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    (install_dir / "DeskSentry.exe").write_bytes(b"existing executable")
    (install_dir / "VERSION.txt").write_text("old-head\nold deployment time\n", encoding="utf-8")
    (install_dir / "unins000.exe").write_bytes(b"existing uninstaller")
    (install_dir / "unins000.dat").write_bytes(b"existing uninstall metadata")
    before = {path.name: path.read_bytes() for path in install_dir.iterdir()}
    _install_fake_pyinstaller(repo, test_env)
    wrapper_env = test_env.copy()
    wrapper_env["TEST_DEPLOY_SCRIPT"] = str(repo / "tools" / "deploy.ps1")
    wrapper_env["TEST_INSTALL_DIR"] = str(install_dir)

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "function global:Get-CimInstance { @() }; "
                "function global:Copy-Item { [CmdletBinding()] param("
                "[string]$LiteralPath,[string]$Destination,[switch]$Recurse,[switch]$Force); "
                "if ($LiteralPath -like '*\\.tmp\\deploy\\dist\\DeskSentry') "
                "{ throw 'injected staging copy failure' }; "
                "Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters }; "
                "& $env:TEST_DEPLOY_SCRIPT -InstallDir $env:TEST_INSTALL_DIR "
                "-Force -SkipRegistry -NoStart"
            ),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=wrapper_env,
    )
    output = _output(result)

    assert result.returncode != 0, output
    assert "[rollback]" in output and "injected staging copy failure" in output
    after = {path.name: path.read_bytes() for path in install_dir.iterdir()}
    assert after == before
    assert not Path(f"{install_dir}.prev").exists()


def test_deploy_preserves_inno_owned_files_across_two_swaps(deploy_repo):
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    original = {
        "unins000.exe": b"original uninstaller",
        "unins000.dat": b"original data",
        "unins000.msg": b"original messages",
    }
    for name, content in original.items():
        (install_dir / name).write_bytes(content)
    (install_dir / "DeskSentry.exe").write_bytes(b"old executable")
    _install_fake_pyinstaller(repo, test_env)
    wrapper_env = test_env | {
        "TEST_DEPLOY_SCRIPT": str(repo / "tools/deploy.ps1"),
        "TEST_INSTALL_DIR": str(install_dir),
    }
    for _ in range(2):
        result = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "function global:Get-CimInstance { @() }; "
                "& $env:TEST_DEPLOY_SCRIPT -Force -SkipRegistry -NoStart "
                "-InstallDir $env:TEST_INSTALL_DIR",
            ],
            cwd=repo,
            env=wrapper_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, _output(result)
        for name, content in original.items():
            assert (install_dir / name).read_bytes() == content
        assert (install_dir / "VERSION.txt").read_text(encoding="utf-8-sig").startswith("2.0.0+")


def test_deploy_requests_graceful_quit_before_wait_and_fallback(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, install_dir, test_env = deploy_repo
    install_dir.mkdir()
    installed_exe = install_dir / "DeskSentry.exe"
    installed_exe.write_bytes(b"existing executable")
    (install_dir / "VERSION.txt").write_text("old-head\nold time\n", encoding="utf-8")
    _install_fake_pyinstaller(repo, test_env)
    marker = repo.parent / "process-sequence.txt"
    wrapper_env = test_env.copy()
    wrapper_env["TEST_DEPLOY_SCRIPT"] = str(repo / "tools" / "deploy.ps1")
    wrapper_env["TEST_INSTALL_DIR"] = str(install_dir)
    wrapper_env["TEST_PROCESS_MARKER"] = str(marker)
    wrapper_env["TEST_MATCHING_EXE"] = str(installed_exe)
    wrapper_env["TEST_OTHER_EXE"] = str(repo.parent / "other" / "DeskSentry.exe")

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "function global:Get-CimInstance { @("
                "[pscustomobject]@{ProcessId=11;ExecutablePath=$env:TEST_MATCHING_EXE},"
                "[pscustomobject]@{ProcessId=22;ExecutablePath=$env:TEST_OTHER_EXE}) }; "
                "function global:Get-Process { param([int]$Id,[object]$ErrorAction); "
                'if ($Id -ne 11) { throw "unexpected process lookup $Id" }; '
                "$p=[pscustomobject]@{Id=$Id;Path=$env:TEST_MATCHING_EXE}; "
                "$p | Add-Member ScriptMethod WaitForExit { param($ms) "
                'Add-Content $env:TEST_PROCESS_MARKER "wait:$($this.Id):$ms"; '
                "$script:Forced }; "
                "$p | Add-Member ScriptMethod CloseMainWindow { "
                'Add-Content $env:TEST_PROCESS_MARKER "close:$($this.Id)"; $true }; $p }; '
                "function global:Start-Process { [CmdletBinding()] param("
                "[string]$FilePath,[string]$ArgumentList,[string]$WorkingDirectory,"
                "[object]$WindowStyle,[switch]$Wait,[switch]$PassThru); "
                'Add-Content $env:TEST_PROCESS_MARKER "start:${FilePath}:$ArgumentList" }; '
                "function global:Stop-Process { [CmdletBinding()] param("
                "[object]$InputObject,[switch]$Force); "
                'Add-Content $env:TEST_PROCESS_MARKER "force:$($InputObject.Id)"; '
                "$script:Forced=$true }; "
                "& $env:TEST_DEPLOY_SCRIPT -InstallDir $env:TEST_INSTALL_DIR "
                "-Force -SkipRegistry -NoStart"
            ),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env=wrapper_env,
    )
    output = _output(result)

    assert result.returncode == 0, output
    events = marker.read_text(encoding="utf-8-sig").splitlines()
    assert events == [
        f"start:{installed_exe}:--quit",
        "wait:11:10000",
        "close:11",
        "wait:11:1000",
        "force:11",
        "wait:11:10000",
    ]
    version_lines = (install_dir / "VERSION.txt").read_text(encoding="utf-8-sig").splitlines()
    staged_info = json.loads(
        (install_dir / "_internal" / "desk_sentry" / "_build_info.json").read_text(encoding="utf-8")
    )
    assert version_lines[0] == staged_info["display_version"]
    assert version_lines[1] == _run_git(repo, "rev-parse", "HEAD").stdout.strip()
    assert len(version_lines) == 3


@pytest.mark.skipif(GIT_SH is None, reason="Git for Windows sh is unavailable")
def test_post_commit_on_non_main_branch_does_not_launch_deploy(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, _, test_env = deploy_repo
    _run_git(repo, "switch", "-c", "feature/test-hook")
    stub_bin = repo.parent / "stub-bin"
    stub_bin.mkdir()
    marker = repo.parent / "powershell-launched.txt"
    (stub_bin / "powershell.exe").write_text(
        '#!/bin/sh\nprintf "launched" > "$DEPLOY_HOOK_MARKER"\n',
        encoding="utf-8",
        newline="\n",
    )
    test_env["DEPLOY_HOOK_MARKER"] = str(marker)
    test_env["PATH"] = f"{stub_bin}{os.pathsep}{test_env['PATH']}"

    result = subprocess.run(
        [str(GIT_SH), str(repo / "tools" / "git-hooks" / "post-commit")],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
        env=test_env,
    )

    assert result.returncode == 0, _output(result)
    assert not marker.exists()


@pytest.mark.skipif(GIT_SH is None, reason="Git for Windows sh is unavailable")
def test_post_commit_on_main_branch_launches_powershell_in_background(
    deploy_repo: tuple[Path, Path, dict[str, str]],
) -> None:
    repo, _, test_env = deploy_repo
    stub_bin = repo.parent / "stub-bin"
    stub_bin.mkdir()
    started = repo / "hook-started.txt"
    release = repo / "hook-release.txt"
    completed = repo / "hook-completed.txt"
    (stub_bin / "powershell.exe").write_text(
        (
            '#!/bin/sh\nprintf "started" > hook-started.txt\n'
            "while [ ! -e hook-release.txt ]; do sleep 0.025; done\n"
            'printf "completed" > hook-completed.txt\n'
        ),
        encoding="utf-8",
        newline="\n",
    )
    test_env["PATH"] = f"{stub_bin}{os.pathsep}{test_env['PATH']}"

    hook = subprocess.Popen(
        [str(GIT_SH), str(repo / "tools" / "git-hooks" / "post-commit")],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=test_env,
    )
    try:
        assert _wait_for_file(started), "background PowerShell boundary did not start"
        try:
            hook_stdout, hook_stderr = hook.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail("post-commit waited for the gated background deploy")
        assert hook.returncode == 0, f"{hook_stdout}\n{hook_stderr}"
        assert not completed.exists(), "background deploy completed before its release gate"
    finally:
        release.write_text("release\n", encoding="utf-8")
        if hook.poll() is None:
            hook.communicate(timeout=10)
        assert _wait_for_file(completed), "background stub did not exit after release"
