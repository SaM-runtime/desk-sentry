"""Scan publishable files and, by default, the current branch's Git history.

Binary assets still require visual review. Deleted tracked paths are skipped,
matching the tree that staging all changes would produce. No Git writes occur.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "local-path": re.compile(r"C:[/\\]+Users[/\\]", re.I),
    "internal-record": re.compile(r"\.(?:co" + r"dex|con" + r"sole|ta" + r"sk)\b", re.I),
}
EMAIL = re.compile(r"[\w.+%~-]+@[\w.-]+\.[A-Za-z]{2,}")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Exact file and upstream address; never exempt an entire licensing directory.
UPSTREAM_EMAILS = {
    "licenses/YuNet-OpenCV-Zoo-MIT.txt": {"shiqi.yu" + "@gmail.com"},
}


def load_extra() -> list[re.Pattern[str]]:
    path = Path(os.environ.get("PRIVACY_SCAN_EXTRA", ROOT / ".git/info/privacy-patterns.txt"))
    if not path.exists():
        print("Private pattern file absent; optional rules skipped.")
        return []
    return [
        re.compile(line.strip(), re.I)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def findings(name: str, text: str, extra: tuple | list = ()) -> list[str]:
    hits = [
        label
        for label, pattern in PATTERNS.items()
        if not (name == ".gitignore" and label == "internal-record") and pattern.search(text)
    ]
    for match in EMAIL.finditer(text):
        email = match.group()
        if email in UPSTREAM_EMAILS.get(name, set()):
            continue
        if email.lower().endswith("@users.noreply.github.com"):
            continue
        # Public no-reply address used in AI co-author commit trailers.
        if email.lower() == "noreply@anthropic.com":
            continue
        hits.append("email")
    for match in IPV4.finditer(text):
        octets = tuple(map(int, match.group().split(".")))
        if max(octets) <= 255 and (
            octets[0] == 10
            or octets[:2] == (192, 168)
            or (octets[0] == 172 and 16 <= octets[1] <= 31)
        ):
            hits.append("private-ip")
    if any(pattern.search(text) for pattern in extra):
        hits.append("private")
    return sorted(set(hits))


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=True, timeout=60
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", action="store_true", help="Skip historical commit metadata")
    args = parser.parse_args()
    failures = []
    try:
        extra = load_extra()
        names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for raw in sorted(set(names.split(b"\0")) - {b""}):
            name = raw.decode("utf-8", errors="surrogateescape")
            path = ROOT / name
            if not path.exists() and not path.is_symlink():
                continue
            for label in findings("<filename>", name, extra):
                failures.append(f"{name}:0:{label}")
            if path.is_symlink():
                failures.append(f"{name}:0:symlink-review-required")
                continue
            data = path.read_bytes()
            # UTF-16 text must not bypass the scan as binary data.
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = data.decode(encoding, errors="replace")
            for line, value in enumerate(text.splitlines(), 1):
                for label in findings(name, value, extra):
                    failures.append(f"{name}:{line}:{label}")
        if not args.worktree:
            log = git("log", "--format=%an%n%ae%n%cn%ce%n%B", "HEAD").decode("utf-8")
            for line, value in enumerate(log.splitlines(), 1):
                for label in findings("<git-log>", value, extra):
                    failures.append(f"<git-log>:{line}:{label}")
    except (OSError, subprocess.SubprocessError, UnicodeError, re.error) as exc:
        print(f"scan:0:read-error:{type(exc).__name__}")
        return 1
    for failure in failures:
        print(failure)
    print(f"Privacy scan: {len(failures)} finding(s)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
