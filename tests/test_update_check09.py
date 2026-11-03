from __future__ import annotations

import json
import logging
import urllib.error
from typing import Any

import pytest


class _Response:
    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _RawResponse(_Response):
    def __init__(self, body: bytes) -> None:
        self._body = body


def _release(
    version: str,
    *,
    draft: bool = False,
    prerelease: bool = False,
    html_url: str = "https://attacker.invalid/not-used",
) -> dict[str, object]:
    return {
        "tag_name": f"v{version}",
        "draft": draft,
        "prerelease": prerelease,
        "html_url": html_url,
    }


def test_disabled_and_development_versions_do_not_touch_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import desk_sentry.update_check as updates

    calls: list[object] = []
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *args, **kwargs: calls.append(args)
    )

    disabled = updates.check_for_update("2.0.0", enabled=False, now=10.0)
    development = updates.check_for_update("2.0.0+abc1234", enabled=True, now=10.0)

    assert disabled.attempted is False
    assert development.attempted is False
    assert calls == []


def test_automatic_check_is_throttled_for_24_hours_but_manual_bypasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import desk_sentry.update_check as updates

    calls: list[object] = []

    def respond(*args: object, **kwargs: object) -> _Response:
        calls.append(args)
        return _Response(_release("2.0.0"))

    monkeypatch.setattr(updates.urllib.request, "urlopen", respond)

    throttled = updates.check_for_update(
        "2.0.0", enabled=True, last_checked=100.0, now=100.0 + 86_399
    )
    manual = updates.check_for_update(
        "2.0.0", enabled=True, last_checked=100.0, now=101.0, manual=True
    )

    assert throttled.attempted is False
    assert manual.attempted is True
    assert manual.checked_at == 101.0
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_draft_and_prerelease_releases_are_ignored(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    import desk_sentry.update_check as updates

    payload = _release("3.0.0", **{field: True})
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *args, **kwargs: _Response(payload)
    )

    result = updates.check_for_update("2.0.0", enabled=True, now=200.0)

    assert result.attempted is True
    assert result.available is None


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("2.0.1", "2.0.0", True),
        ("2.0.0", "2.0.0", False),
        ("2.0.0+release.2", "2.0.0+release.1", False),
        ("2.0.0", "2.0.0-rc.1", True),
        ("2.0.0-beta.11", "2.0.0-beta.2", True),
        ("2.0.0-beta.2", "2.0.0-beta.11", False),
        ("2.0.0-alpha", "2.0.0-alpha.1", False),
        ("2.0.0-alpha.1", "2.0.0-alpha.beta", False),
        ("2.1.0", "2.0.99", True),
    ],
)
def test_semver_precedence(candidate: str, current: str, expected: bool) -> None:
    from desk_sentry.update_check import is_newer_version

    assert is_newer_version(candidate, current) is expected


def test_new_release_uses_fixed_github_url_and_minimal_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import desk_sentry.update_check as updates

    observed: dict[str, object] = {}

    def respond(request: object, *, timeout: float) -> _Response:
        observed["request"] = request
        observed["timeout"] = timeout
        return _Response(_release("2.1.0"))

    monkeypatch.setattr(updates.urllib.request, "urlopen", respond)

    result = updates.check_for_update("2.0.0", enabled=True, now=300.0)

    assert result.available == updates.AvailableRelease(
        version="2.1.0",
        url="https://github.com/SaM-runtime/desk-sentry/releases/tag/v2.1.0",
    )
    request = observed["request"]
    assert isinstance(request, updates.urllib.request.Request)
    assert (
        request.full_url == "https://api.github.com/repos/SaM-runtime/desk-sentry/releases/latest"
    )
    assert dict(request.header_items()) == {"User-agent": "DeskSentry/2.0.0"}
    assert observed["timeout"] == 5.0


def test_ignored_release_is_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    import desk_sentry.update_check as updates

    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(_release("2.1.0")),
    )

    result = updates.check_for_update("2.0.0", enabled=True, ignored_version="2.1.0", now=400.0)

    assert result.available is None


@pytest.mark.parametrize(
    "failure",
    [OSError("offline"), TimeoutError("slow"), ValueError("bad response")],
)
def test_network_errors_are_silent_attempts(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    import desk_sentry.update_check as updates

    def fail(*args: object, **kwargs: object) -> _Response:
        raise failure

    monkeypatch.setattr(updates.urllib.request, "urlopen", fail)

    result = updates.check_for_update("2.0.0", enabled=True, now=500.0)

    assert result == updates.UpdateCheckResult(True, 500.0, None, status="error")


@pytest.mark.parametrize(
    "response",
    [
        _RawResponse(b"not json"),
        urllib.error.HTTPError("https://api.github.com/", 429, "rate limited", hdrs=None, fp=None),
    ],
)
def test_invalid_json_and_rate_limit_are_logged_without_escaping(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    response: _Response | urllib.error.HTTPError,
) -> None:
    import desk_sentry.update_check as updates

    caplog.set_level(logging.INFO, logger=updates.__name__)

    def respond(*args: object, **kwargs: object) -> _Response:
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(updates.urllib.request, "urlopen", respond)

    result = updates.check_for_update("2.0.0", enabled=True, now=600.0)

    assert result.available is None
    assert result.attempted is True
    assert "Update check failed" in caplog.text
