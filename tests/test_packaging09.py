from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

import desk_sentry
from desk_sentry.project import GITHUB_REPOSITORY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = PROJECT_ROOT / "packaging" / "build_release.py"
INSTALLER_PATH = PROJECT_ROOT / "packaging" / "installer.iss"
RELEASE_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "release.yml"


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("desk_sentry_build_release", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_metadata_generates_build_info_and_project_defines(tmp_path: Path) -> None:
    helper = _load_helper()

    metadata = helper.prepare_metadata(tmp_path, release=True, root=PROJECT_ROOT)

    assert metadata["version"] == metadata["display_version"] == desk_sentry.__version__
    assert metadata["repository"] == GITHUB_REPOSITORY
    persisted = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert persisted == metadata
    build_info = json.loads((tmp_path / "_build_info.json").read_text(encoding="utf-8"))
    assert build_info["display_version"] == desk_sentry.__version__
    assert not (tmp_path / "version-info.txt").exists()
    defines = (tmp_path / "installer-defines.iss").read_text(encoding="utf-8")
    assert f'#define MyAppVersion "{desk_sentry.__version__}"' in defines
    assert f'#define MyRepositoryUrl "https://github.com/{GITHUB_REPOSITORY}"' in defines
    assert str((tmp_path / "dist" / "DeskSentry").resolve()) in defines


def test_check_tag_cli_rejects_version_mismatch() -> None:
    expected_tag = f"v{desk_sentry.__version__}"
    good = subprocess.run(
        [sys.executable, str(HELPER_PATH), "check-tag", "--tag", expected_tag],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    bad = subprocess.run(
        [sys.executable, str(HELPER_PATH), "check-tag", "--tag", "v9.9.9"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert good.returncode == 0, good.stderr
    assert bad.returncode != 0
    assert "does not match" in bad.stderr


def test_prepare_cli_uses_release_environment_switch(tmp_path: Path) -> None:
    release_output = tmp_path / "release"
    development_output = tmp_path / "development"
    release_env = os.environ.copy()
    release_env["DESKSENTRY_RELEASE"] = "1"

    release = subprocess.run(
        [sys.executable, str(HELPER_PATH), "prepare", "--output", str(release_output)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=release_env,
    )
    development_env = os.environ.copy()
    development_env.pop("DESKSENTRY_RELEASE", None)
    development = subprocess.run(
        [
            sys.executable,
            str(HELPER_PATH),
            "prepare",
            "--output",
            str(development_output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=development_env,
    )

    assert release.returncode == development.returncode == 0
    release_metadata = json.loads((release_output / "metadata.json").read_text(encoding="utf-8"))
    development_metadata = json.loads(
        (development_output / "metadata.json").read_text(encoding="utf-8")
    )
    assert release_metadata["display_version"] == desk_sentry.__version__
    assert release_metadata["release"] is True
    assert development_metadata["display_version"].startswith(f"{desk_sentry.__version__}+")
    assert development_metadata["release"] is False


def test_package_creates_portable_zip_checksums_and_release_notes(tmp_path: Path) -> None:
    helper = _load_helper()
    distribution = tmp_path / "dist" / "DeskSentry"
    (distribution / "python").mkdir(parents=True)
    (distribution / "python" / "python.exe").write_bytes(b"official python")
    (distribution / "python" / "pythonw.exe").write_bytes(b"official pythonw")
    (distribution / "app" / "desk_sentry").mkdir(parents=True)
    (distribution / "app" / "desk_sentry" / "__init__.py").write_text("", encoding="utf-8")
    (distribution / "THIRD_PARTY_NOTICES.md").write_text(
        "fixture notices\n", encoding="utf-8", newline="\n"
    )
    installer = tmp_path / f"DeskSentry-{desk_sentry.__version__}-Setup.exe"
    installer.write_bytes(b"installer")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        f"# Changelog\n\n## [{desk_sentry.__version__}] - 2026-10-20\n\n"
        "### Added\n- Release.\n\n"
        "## [1.0.0] - 2026-01-01\n\n- Old.\n",
        encoding="utf-8",
    )
    output = tmp_path / "release"

    artifacts = helper.package_release(distribution, installer, output, changelog)

    portable = output / f"DeskSentry-{desk_sentry.__version__}-portable.zip"
    with zipfile.ZipFile(portable) as archive:
        assert archive.namelist() == [
            "DeskSentry/THIRD_PARTY_NOTICES.md",
            "DeskSentry/app/desk_sentry/__init__.py",
            "DeskSentry/python/python.exe",
            "DeskSentry/python/pythonw.exe",
        ]
        assert archive.read("DeskSentry/THIRD_PARTY_NOTICES.md") == b"fixture notices\n"
    notes = (output / "release-notes.md").read_text(encoding="utf-8")
    assert f"## [{desk_sentry.__version__}]" in notes and "## [1.0.0]" not in notes
    checksum_lines = (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    assert checksum_lines == [
        f"{hashlib.sha256(installer.read_bytes()).hexdigest()}  {installer.name}",
        f"{hashlib.sha256(portable.read_bytes()).hexdigest()}  {portable.name}",
    ]
    assert artifacts["portable"] == portable


def test_release_packaging_rejects_missing_notices_and_installer_requires_them(tmp_path):
    helper = _load_helper()
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "python").mkdir()
    (distribution / "python" / "pythonw.exe").write_bytes(b"fixture")
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="THIRD_PARTY_NOTICES.md"):
        helper.package_release(distribution, installer, tmp_path / "out", tmp_path / "changelog")
    script = INSTALLER_PATH.read_text(encoding="utf-8")
    assert 'Source: "{#MySourceDir}\\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"' in script


def test_project_links_managed_block_is_derived_from_repository_constant(tmp_path: Path) -> None:
    helper = _load_helper()
    readme = tmp_path / "README.md"
    readme.write_text(
        "before\n<!-- desk-sentry:project-links:start -->\nold\n"
        "<!-- desk-sentry:project-links:end -->\nafter\n",
        encoding="utf-8",
    )

    helper.sync_project_links(readme)

    rendered = helper.render_project_links()
    assert rendered in readme.read_text(encoding="utf-8")
    assert f"https://github.com/{GITHUB_REPOSITORY}/releases/latest" in rendered
    assert helper.check_project_links(readme) is True


def test_installer_enforces_fixed_location_startup_and_safe_uninstall() -> None:
    installer = INSTALLER_PATH.read_text(encoding="utf-8")

    required = (
        "AppId={{7EAFD7CD-908C-4F1B-AB4B-8CB50A948B59}",
        "PrivilegesRequired=lowest",
        r"DefaultDirName={localappdata}\Programs\DeskSentry",
        "DisableDirPage=yes",
        "UsePreviousAppDir=no",
        "AppMutex=DeskSentry.SingleInstance",
        "CloseApplications=no",
        'ValueName: "DeskSentry"',
        r'ValueData: """{app}\python\pythonw.exe"" -m desk_sentry --minimized"',
        "uninsdeletevalue",
        "deletevalue",
        r"{userappdata}\DeskSentry",
        "UninstallSilent",
        r'Type: files; Name: "{app}\VERSION.txt"',
        r'Type: files; Name: "{app}\DeskSentry.exe"',
        r'Type: filesandordirs; Name: "{app}\_internal"',
    )
    for value in required:
        assert value in installer
    startup_task = next(
        line for line in installer.splitlines() if line.startswith('Name: "startup"')
    )
    assert "checkedonce" not in startup_task
    unconditional_cleanup = next(
        line
        for line in installer.splitlines()
        if "ValueType: none" in line and "uninsdeletevalue" in line
    )
    assert "Tasks:" not in unconditional_cleanup and "Check:" not in unconditional_cleanup
    assert "CloseApplicationsFilter" not in installer
    assert r"UninstallDisplayIcon={app}\desk_sentry.ico" in installer
    shortcut = next(
        line for line in installer.splitlines() if line.startswith('Name: "{userprograms}')
    )
    assert r'Filename: "{app}\python\pythonw.exe"' in shortcut
    assert 'Parameters: "-m desk_sentry"' in shortcut
    assert r'IconFilename: "{app}\desk_sentry.ico"' in shortcut
    request = installer.index("procedure RequestGracefulQuit")
    quit_command = installer.index("'-m desk_sentry --quit'", request)
    mutex_wait = installer.index("CheckForMutexes('DeskSentry.SingleInstance')", request)
    assert quit_command < mutex_wait
    initialize = installer.index("function InitializeSetup")
    assert "RequestGracefulQuit;" in installer[initialize : initialize + 300]
    assert r"{localappdata}\Programs\DeskSentry\python\pythonw.exe" in installer
    assert r"{localappdata}\Programs\DeskSentry\DeskSentry.exe" in installer


def test_release_workflow_builds_unpacked_bundle_without_pyinstaller() -> None:
    workflow = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "build_release.py bundle" in workflow
    assert "--output .tmp/release/dist/DeskSentry" in workflow
    assert "--metadata-dir .tmp/release" in workflow
    assert "PyInstaller" not in workflow


def test_notice_model_license_references_exist():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    paths = re.findall(r"`(licenses/[^`]+)`", notice)
    assert len(paths) >= 3
    for path in paths:
        assert (root / path).is_file(), path
