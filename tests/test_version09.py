from __future__ import annotations

import email.parser
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import desk_sentry
from desk_sentry.build_info import BuildInfo, collect_build_info, get_build_info, write_build_info
from desk_sentry.project import GITHUB_REPOSITORY

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_setuptools_metadata_uses_package_version(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "desk_sentry"
    package.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", project / "pyproject.toml")
    shutil.copy2(PROJECT_ROOT / "README.md", project / "README.md")
    shutil.copy2(PROJECT_ROOT / "src" / "desk_sentry" / "__init__.py", package / "__init__.py")
    metadata_dir = project / "metadata"
    metadata_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import setuptools.build_meta as backend; "
                "print(backend.prepare_metadata_for_build_wheel('metadata'))"
            ),
        ],
        cwd=project,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    dist_info = result.stdout.strip().splitlines()[-1]
    metadata_text = (metadata_dir / dist_info / "METADATA").read_text(encoding="utf-8")
    metadata = email.parser.Parser().parsestr(metadata_text)

    assert metadata["Version"] == desk_sentry.__version__
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'attr = "desk_sentry.__version__"' in pyproject


def test_github_repository_has_one_canonical_value() -> None:
    assert GITHUB_REPOSITORY == "SaM-runtime/desk-sentry"


def test_build_info_display_version_distinguishes_release_and_development() -> None:
    release = BuildInfo(
        version="2.0.0",
        commit="abc1234",
        dirty=False,
        built_at="2026-10-20T08:00:00Z",
        release=True,
    )
    development = BuildInfo(
        version="2.0.0",
        commit="abc1234",
        dirty=False,
        built_at="2026-10-20T08:00:00Z",
        release=False,
    )
    dirty = BuildInfo(
        version="2.0.0",
        commit="abc1234",
        dirty=True,
        built_at="2026-10-20T08:00:00Z",
        release=False,
    )

    assert release.display_version == "2.0.0"
    assert development.display_version == "2.0.0+abc1234"
    assert dirty.display_version == "2.0.0+abc1234.dirty"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def test_collect_build_info_reads_source_git_state(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "test@users.noreply.github.com")
    _git(tmp_path, "config", "user.name", "Desk Sentry Test")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "fixture")
    expected_commit = _git(tmp_path, "rev-parse", "--short=7", "HEAD")

    clean = collect_build_info(tmp_path)
    tracked.write_text("dirty\n", encoding="utf-8")
    dirty = collect_build_info(tmp_path)

    assert clean.version == desk_sentry.__version__
    assert clean.commit == expected_commit
    assert clean.dirty is False
    assert dirty.commit == expected_commit
    assert dirty.dirty is True
    assert datetime.fromisoformat(clean.built_at.replace("Z", "+00:00")).tzinfo == UTC


def test_get_build_info_reads_generated_frozen_resource(tmp_path: Path) -> None:
    resource = tmp_path / "_build_info.json"
    expected = BuildInfo(
        version="2.0.0",
        commit="def5678",
        dirty=False,
        built_at="2026-10-20T08:01:00Z",
        release=True,
    )

    write_build_info(resource, expected)
    loaded = get_build_info(resource_path=resource, source_root=tmp_path / "missing")

    assert loaded == expected
    payload = json.loads(resource.read_text(encoding="utf-8"))
    assert payload["version"] == "2.0.0"
    assert payload["display_version"] == "2.0.0"


def test_collect_build_info_without_git_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git_is_unavailable(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", git_is_unavailable)
    info = collect_build_info(tmp_path)

    assert info.version == desk_sentry.__version__
    assert info.commit == "unknown"
    assert info.dirty is False
    assert info.display_version == "2.0.0+unknown"
