"""Prepare Desk Sentry build metadata and release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from desk_sentry import __version__  # noqa: E402
from desk_sentry.build_info import collect_build_info, write_build_info  # noqa: E402
from desk_sentry.project import GITHUB_REPOSITORY  # noqa: E402

PROJECT_LINKS_START = "<!-- desk-sentry:project-links:start -->"
PROJECT_LINKS_END = "<!-- desk-sentry:project-links:end -->"

# SHA256 from python.org's matching .zip.spdx.json (CPython package entry).
PYTHON_VERSION = "3.12.10"
PYTHON_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
)
PYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
# Testers often double-click the launcher inside the zip without extracting it,
# or antivirus quarantines pythonw.exe; explain instead of a bare "not found".
_LAUNCHER_LINES = (
    "@echo off",
    "chcp 65001 >nul",
    'cd /d "%~dp0"',
    'if exist "python\\pythonw.exe" goto run',
    "echo.",
    "echo 找不到 python\\pythonw.exe，Desk Sentry 無法啟動。",
    "echo.",
    "echo 常見原因：",
    "echo   1. 還沒解壓縮：請關掉這個視窗，在 zip 檔上按右鍵，選「解壓縮全部」，",
    "echo      再到解壓出來的 DeskSentry 資料夾裡雙擊「啟動 Desk Sentry」。",
    "echo   2. 防毒軟體把 pythonw.exe 隔離了：請截圖防毒的通知畫面回報。",
    "echo.",
    "echo 目前位置：%~dp0",
    "echo.",
    "pause",
    "exit /b 1",
    ":run",
    'start "" "%~dp0python\\pythonw.exe" -m desk_sentry',
)

_UNUSED_BUNDLE_PATTERNS = (
    "cv2/opencv_videoio_ffmpeg*.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/Qt6Quick*.dll",
    "PySide6/Qt6Qml*.dll",
    "PySide6/Qt6Labs*.dll",
    "PySide6/Qt6Lottie*.dll",
    "PySide6/Qt6Designer*.dll",
    "PySide6/Qt6OpenGL*.dll",
    "PySide6/QtOpenGL*.pyd",
    "PySide6/QtQuick*.pyd",
    "PySide6/QtQml*.pyd",
    "PySide6/QtDesigner*.pyd",
    "PySide6/resources/icudtl.dat",
    "PySide6/translations/qtwebengine*",
)


def bundle_prune_patterns() -> tuple[str, ...]:
    """Return runtime files verified unused by the application source tree."""

    return _UNUSED_BUNDLE_PATTERNS


def _prune_unused_bundle_files(site: Path) -> None:
    for pattern in bundle_prune_patterns():
        for path in site.glob(pattern):
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def _install_dependencies(target: Path, root: Path) -> None:
    dependencies = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    if not dependencies or any("==" not in dep for dep in dependencies):
        raise ValueError("Runtime dependencies must be pinned")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target),
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            "3.12",
            "--implementation",
            "cp",
            "--no-compile",
            "--disable-pip-version-check",
            *dependencies,
        ],
        check=True,
        timeout=600,
    )


def bundle(
    output: Path, metadata_dir: Path, *, python_zip: Path | None = None, root: Path = ROOT
) -> Path:
    """Build in a temporary sibling; never publish a partial or unverified tree."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Bundle output already exists; choose a fresh output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-", dir=output.parent) as temporary:
        staging = Path(temporary)
        archive_path = python_zip
        if archive_path is None:
            archive_path = staging / "python.zip"
            with urllib.request.urlopen(PYTHON_URL, timeout=60) as response:
                archive_path.write_bytes(response.read())
        if _sha256(archive_path) != PYTHON_SHA256:
            raise ValueError("Python archive SHA256 mismatch")
        tree = staging / "DeskSentry"
        interpreter = tree / "python"
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                destination = (interpreter / item.filename).resolve()
                if not destination.is_relative_to(interpreter.resolve()):
                    raise ValueError("Unsafe archive path")
            archive.extractall(interpreter)
        for name in ("python.exe", "pythonw.exe", "python312._pth", "LICENSE.txt"):
            if not (interpreter / name).is_file():
                raise ValueError(f"Incomplete embedded Python: {name}")
        official_exes = {
            p.relative_to(tree).as_posix(): _sha256(p)
            for p in interpreter.rglob("*")
            if p.suffix.lower() == ".exe"
        }
        (interpreter / "python312._pth").write_text(
            "python312.zip\n.\n..\\app\nLib\\site-packages\nimport site\n", encoding="utf-8"
        )
        site = interpreter / "Lib/site-packages"
        _install_dependencies(site, root)
        _prune_unused_bundle_files(site)
        # Wheel command-line tools are not app runtime dependencies; never ship launchers.
        for path in list(site.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".exe":
                path.unlink()
        for path in site.iterdir():
            if path.name.lower().split("-")[0] in {"pip", "setuptools", "pkg_resources"}:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        # The app uses Qt Widgets only; PySide6's QML tree is unused and its deep
        # build-artifact paths can exceed Windows MAX_PATH after "Extract All".
        shutil.rmtree(site / "PySide6/qml", ignore_errors=True)
        shutil.copytree(
            root / "src/desk_sentry",
            tree / "app/desk_sentry",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
        )
        prepare_metadata(metadata_dir, root=root)
        shutil.copy2(
            resolve_metadata_dir(metadata_dir, root=root) / "_build_info.json",
            tree / "app/desk_sentry/_build_info.json",
        )
        # Use the same pinned models as the explicit model preparation tool.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "bundle_models", root / "tools/fetch_model.py"
        )
        assert spec is not None and spec.loader is not None
        models = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = models
        spec.loader.exec_module(models)
        (tree / "models").mkdir()
        for model in models.PINNED:
            source = root / "models" / model.name
            if _sha256(source) != model.sha256:
                raise ValueError(f"Model SHA256 mismatch: {model.name}")
            shutil.copy2(source, tree / "models" / model.name)
        shutil.copytree(root / "licenses", tree / "licenses")
        shutil.copy2(interpreter / "LICENSE.txt", tree / "licenses/Python-PSF-License.txt")
        shutil.copytree(root / "packaging/licenses", tree / "licenses/runtime")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(root / name, tree / name)
        shutil.copy2(root / "packaging/desk_sentry.ico", tree / "desk_sentry.ico")
        (tree / "啟動 Desk Sentry.cmd").write_bytes(
            "\r\n".join(_LAUNCHER_LINES).encode("utf-8") + b"\r\n"
        )
        actual_exes = {
            p.relative_to(tree).as_posix(): _sha256(p)
            for p in tree.rglob("*")
            if p.suffix.lower() == ".exe"
        }
        if actual_exes != official_exes:
            raise ValueError("Unexpected executable in bundle")
        tree.rename(output)
    return output


def _iss_quote(value: str) -> str:
    return value.replace('"', '""')


def _installer_defines(output: Path, version: str) -> str:
    source = (output / "dist" / "DeskSentry").resolve()
    repository_url = f"https://github.com/{GITHUB_REPOSITORY}"
    return (
        f'#define MyAppVersion "{_iss_quote(version)}"\n'
        f'#define MyRepositoryUrl "{_iss_quote(repository_url)}"\n'
        f'#define MySourceDir "{_iss_quote(str(source))}"\n'
        f'#define MyOutputDir "{_iss_quote(str(output.resolve()))}"\n'
    )


def resolve_metadata_dir(output: Path, *, root: Path = ROOT) -> Path:
    path = Path(output)
    return (path if path.is_absolute() else Path(root) / path).resolve()


def prepare_metadata(
    output: Path, *, release: bool | None = None, root: Path = ROOT
) -> dict[str, Any]:
    """Write build identity and Inno Setup inputs."""
    output = resolve_metadata_dir(output, root=root)
    output.mkdir(parents=True, exist_ok=True)
    if release is None:
        release = os.environ.get("DESKSENTRY_RELEASE") == "1"
    info = collect_build_info(root, release=release)
    build_info_path = output / "_build_info.json"
    write_build_info(build_info_path, info)
    metadata: dict[str, Any] = asdict(info)
    metadata["display_version"] = info.display_version
    metadata["repository"] = GITHUB_REPOSITORY
    metadata["repository_url"] = f"https://github.com/{GITHUB_REPOSITORY}"
    metadata["latest_release_url"] = f"{metadata['repository_url']}/releases/latest"
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "installer-defines.iss").write_text(
        _installer_defines(output, info.version), encoding="utf-8"
    )
    return metadata


def check_tag(tag: str) -> None:
    expected = f"v{__version__}"
    if tag != expected:
        raise ValueError(f"tag {tag!r} does not match package version {expected!r}")


def _extract_changelog(changelog: Path, version: str) -> str:
    lines = changelog.read_text(encoding="utf-8").splitlines()
    heading = f"## [{version}]"
    start = next((index for index, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise ValueError(f"CHANGELOG has no {heading} section")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## [")),
        len(lines),
    )
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(
    distribution: Path,
    installer: Path,
    output: Path,
    changelog: Path,
) -> dict[str, Path]:
    """Create the portable archive, release notes, and checksums."""
    distribution = Path(distribution).resolve()
    installer = Path(installer).resolve()
    output = Path(output).resolve()
    if not (distribution / "python/pythonw.exe").is_file():
        raise ValueError(f"distribution has no python/pythonw.exe: {distribution}")
    if not installer.is_file():
        raise ValueError(f"installer does not exist: {installer}")
    notices = distribution / "THIRD_PARTY_NOTICES.md"
    if not notices.is_file() or not notices.read_text(encoding="utf-8").strip():
        raise ValueError("distribution must include nonempty THIRD_PARTY_NOTICES.md")
    output.mkdir(parents=True, exist_ok=True)
    portable = output / f"DeskSentry-{__version__}-portable.zip"
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        files = (item for item in distribution.rglob("*") if item.is_file())
        for path in sorted(files, key=lambda item: item.relative_to(distribution).as_posix()):
            archive.write(path, Path("DeskSentry") / path.relative_to(distribution))
    output_installer = output / installer.name
    if output_installer != installer:
        shutil.copy2(installer, output_installer)
    notes = output / "release-notes.md"
    notes.write_text(_extract_changelog(Path(changelog), __version__), encoding="utf-8")
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in (output_installer, portable)),
        encoding="utf-8",
    )
    return {
        "installer": output_installer,
        "portable": portable,
        "release_notes": notes,
        "checksums": checksums,
    }


def render_project_links() -> str:
    repository_url = f"https://github.com/{GITHUB_REPOSITORY}"
    return "\n".join(
        (
            PROJECT_LINKS_START,
            f"- [下載最新版本]({repository_url}/releases/latest)",
            f"- [專案原始碼]({repository_url})",
            PROJECT_LINKS_END,
        )
    )


def _replace_project_links(text: str) -> str:
    start = text.find(PROJECT_LINKS_START)
    end = text.find(PROJECT_LINKS_END)
    if start < 0 or end < start:
        raise ValueError("README project-links managed markers are missing or out of order")
    end += len(PROJECT_LINKS_END)
    return text[:start] + render_project_links() + text[end:]


def sync_project_links(path: Path) -> None:
    path = Path(path)
    current = path.read_text(encoding="utf-8")
    path.write_text(_replace_project_links(current), encoding="utf-8")


def check_project_links(path: Path) -> bool:
    path = Path(path)
    current = path.read_text(encoding="utf-8")
    return current == _replace_project_links(current)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    bundle_parser = commands.add_parser("bundle")
    bundle_parser.add_argument("--output", type=Path, default=ROOT / "dist/DeskSentry")
    bundle_parser.add_argument("--metadata-dir", type=Path, default=ROOT / ".tmp/bundle-metadata")
    bundle_parser.add_argument("--python-zip", type=Path)
    tag = commands.add_parser("check-tag")
    tag.add_argument("--tag", required=True)
    package = commands.add_parser("package")
    package.add_argument("--dist", type=Path, required=True)
    package.add_argument("--installer", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--changelog", type=Path, required=True)
    for name in ("sync-readme", "check-readme"):
        command = commands.add_parser(name)
        command.add_argument("--path", type=Path, default=ROOT / "README.md")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare_metadata(args.output), ensure_ascii=False))
        elif args.command == "bundle":
            bundle(args.output, args.metadata_dir, python_zip=args.python_zip)
        elif args.command == "check-tag":
            check_tag(args.tag)
        elif args.command == "package":
            artifacts = package_release(args.dist, args.installer, args.output, args.changelog)
            print(json.dumps({key: str(value) for key, value in artifacts.items()}))
        elif args.command == "sync-readme":
            sync_project_links(args.path)
        elif args.command == "check-readme" and not check_project_links(args.path):
            raise ValueError("README project-links block is stale")
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
