# -*- mode: python ; coding: utf-8 -*-
# Build: .venv\Scripts\python.exe -m PyInstaller --noconfirm packaging\DeskSentry.spec

import os
import sys
from pathlib import Path

ROOT = Path(SPEC).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

from build_release import prepare_metadata, resolve_metadata_dir
from third_party_notices import write_distribution_notices

METADATA_DIR = resolve_metadata_dir(
    Path(os.environ.get("DESKSENTRY_METADATA_DIR", ".tmp/build-metadata")), root=ROOT
)
BUILD_METADATA = prepare_metadata(
    METADATA_DIR,
    release=os.environ.get("DESKSENTRY_RELEASE") == "1",
    root=ROOT,
)
BUILD_INFO = METADATA_DIR / "_build_info.json"
VERSION_INFO = METADATA_DIR / "version-info.txt"

a = Analysis(
    [str(ROOT / "src" / "desk_sentry" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "src" / "desk_sentry" / "assets"), "desk_sentry/assets"),
        (str(ROOT / "models" / "object_detection_nanodet_2022nov.onnx"), "models"),
        (str(ROOT / "models" / "face_detection_yunet_2026may.onnx"), "models"),
        (str(ROOT / "models" / "face_recognition_sface_2021dec.onnx"), "models"),
        (str(ROOT / "licenses"), "licenses"),
        (str(ROOT / "LICENSE"), "."),
        (str(BUILD_INFO), "desk_sentry"),
    ],
    hiddenimports=["ssl", "_ssl", "urllib.request"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mypy", "pytest", "pytestqt", "ruff", "tkinter"],
    noarchive=False,
    optimize=1,
)
# Not needed at runtime (~60 MB): FFmpeg video-file reader (we use DirectShow),
# Qt's software OpenGL and Qt translations. TLS/OpenSSL are retained for the
# opt-in GitHub update check.
_DROP = ("opencv_videoio_ffmpeg", "opengl32sw.dll", "PySide6/translations")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    return not any(part in name for part in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeskSentry",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "desk_sentry.ico"),
    contents_directory="_internal",
    version=str(VERSION_INFO),
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DeskSentry")
write_distribution_notices(Path(DISTPATH) / "DeskSentry", root=ROOT)
