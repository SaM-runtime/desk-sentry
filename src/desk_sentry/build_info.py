"""Build identity shared by source runs and packaged executables."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from desk_sentry import __version__

BUILD_INFO_FILENAME = "_build_info.json"
_BUILD_INFO_PATH = Path(__file__).with_name(BUILD_INFO_FILENAME)


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """Version and source state captured for one build."""

    version: str
    commit: str
    dirty: bool
    built_at: str
    release: bool

    @property
    def display_version(self) -> str:
        """Return the user-facing release or development version."""
        if self.release:
            return self.version
        dirty_suffix = ".dirty" if self.dirty else ""
        return f"{self.version}+{self.commit}{dirty_suffix}"


def _run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect_build_info(root: Path, release: bool = False) -> BuildInfo:
    """Collect build identity from a source checkout without changing it."""
    root = Path(root).resolve()
    commit = _run_git(root, "rev-parse", "--short=7", "HEAD") or "unknown"
    status = _run_git(root, "status", "--porcelain") if commit != "unknown" else None
    return BuildInfo(
        version=__version__,
        commit=commit,
        dirty=bool(status),
        built_at=_utc_timestamp(),
        release=release,
    )


def write_build_info(path: Path, info: BuildInfo) -> None:
    """Write build identity for PyInstaller to include as package data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(info)
    payload["display_version"] = info.display_version
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_build_info(path: Path) -> BuildInfo | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        version = raw["version"]
        commit = raw["commit"]
        dirty = raw["dirty"]
        built_at = raw["built_at"]
        release = raw["release"]
        if not isinstance(version, str) or not isinstance(commit, str):
            return None
        if not isinstance(dirty, bool) or not isinstance(built_at, str):
            return None
        if not isinstance(release, bool):
            return None
        return BuildInfo(version, commit, dirty, built_at, release)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _find_source_root() -> Path:
    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate
    return module_path.parent


def get_build_info(
    *, resource_path: Path | None = None, source_root: Path | None = None
) -> BuildInfo:
    """Read packaged build identity, falling back safely to source state."""
    resource = _BUILD_INFO_PATH if resource_path is None else Path(resource_path)
    packaged = _read_build_info(resource) if resource.is_file() else None
    if packaged is not None:
        return packaged
    root = _find_source_root() if source_root is None else Path(source_root)
    return collect_build_info(root)
