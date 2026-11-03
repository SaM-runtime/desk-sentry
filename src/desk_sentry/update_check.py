"""Privacy-preserving checks for new Desk Sentry releases."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from desk_sentry.project import GITHUB_REPOSITORY

_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_GITHUB_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SEMVER_RE = re.compile(
    r"(?:v)?"
    r"(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>"
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*"
    r"))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AvailableRelease:
    """A newer stable release that can be offered to the user."""

    version: str
    url: str


@dataclass(frozen=True)
class UpdateCheckResult:
    """The observable outcome of one update-check decision."""

    attempted: bool
    checked_at: float
    available: AvailableRelease | None
    status: str = "latest"
    latest: AvailableRelease | None = None


@dataclass(frozen=True)
class _SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


def _parse_semver(version: str) -> _SemVer:
    match = _SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version: {version!r}")
    prerelease = match.group("prerelease")
    return _SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=tuple(prerelease.split(".")) if prerelease else (),
    )


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left or not right:
        return (not left) - (not right)
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return (int(left_part) > int(right_part)) - (int(left_part) < int(right_part))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_part > right_part) - (left_part < right_part)
    return (len(left) > len(right)) - (len(left) < len(right))


def is_newer_version(candidate: str, current: str) -> bool:
    """Return whether ``candidate`` has higher SemVer precedence than ``current``."""
    candidate_version = _parse_semver(candidate)
    current_version = _parse_semver(current)
    candidate_core = (
        candidate_version.major,
        candidate_version.minor,
        candidate_version.patch,
    )
    current_core = (current_version.major, current_version.minor, current_version.patch)
    if candidate_core != current_core:
        return candidate_core > current_core
    return _compare_prerelease(candidate_version.prerelease, current_version.prerelease) > 0


def _without_v_prefix(version: str) -> str:
    return version[1:] if version.startswith("v") else version


def _latest_release(current_version: str) -> AvailableRelease | None:
    if _GITHUB_REPOSITORY_RE.fullmatch(GITHUB_REPOSITORY) is None:
        raise ValueError("invalid GitHub repository")
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": f"DeskSentry/{current_version}"},
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:
        payload: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub response is not an object")
    if payload.get("draft") is True or payload.get("prerelease") is True:
        return None
    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str):
        raise ValueError("GitHub response has no release tag")
    release_version = _without_v_prefix(tag_name)
    _parse_semver(release_version)
    release_url = (
        f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/"
        f"{urllib.parse.quote(tag_name, safe='')}"
    )
    return AvailableRelease(version=release_version, url=release_url)


def check_for_update(
    current_version: str,
    enabled: bool,
    last_checked: float = 0,
    ignored_version: str = "",
    now: float | None = None,
    manual: bool = False,
) -> UpdateCheckResult:
    """Check for an update when policy allows it.

    Automatic checks require opt-in. A manual action grants consent for this
    request only, including development builds, and never changes the setting.
    """
    development = "+" in current_version
    if not manual and (not enabled or development):
        return UpdateCheckResult(False, 0.0, None, status="skipped")
    checked_at = time.time() if now is None else now
    if not manual and last_checked > 0 and checked_at - last_checked < _CHECK_INTERVAL_SECONDS:
        return UpdateCheckResult(False, 0.0, None, status="skipped")
    try:
        _parse_semver(current_version)
        latest = _latest_release(current_version.split("+", 1)[0])
        status = "latest"
        available = None
        if development:
            status = "development"
        elif latest is not None and is_newer_version(latest.version, current_version):
            if latest.version == _without_v_prefix(ignored_version):
                status = "ignored"
            else:
                status, available = "available", latest
    except Exception as exc:
        _LOGGER.info("Update check failed: %s", exc)
        return UpdateCheckResult(True, checked_at, None, status="error")
    return UpdateCheckResult(True, checked_at, available, status=status, latest=latest)
