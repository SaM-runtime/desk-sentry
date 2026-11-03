"""Generate notices from collected Windows artifacts without executing them."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- build-inventory:start -->"
END = "<!-- build-inventory:end -->"


def _native_version(path: Path) -> str:
    import pefile

    with pefile.PE(str(path), fast_load=True) as pe:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        for group in getattr(pe, "FileInfo", []):
            for info in group:
                for table in getattr(info, "StringTable", []):
                    value = table.entries.get(b"FileVersion", b"")
                    if value:
                        return value.decode("utf-8", errors="replace").strip("\0 ")
    return ""


def _component(path: Path) -> str:
    name, relative = path.name.lower(), path.as_posix().lower()
    if name.startswith(("msvcp", "vcruntime", "api-ms-")) or name == "ucrtbase.dll":
        return "Microsoft MSVC / UCRT"
    if name.startswith(("libcrypto", "libssl")):
        return "OpenSSL"
    if "pyside6/" in relative:
        return "Qt / PySide6"
    if "shiboken6/" in relative:
        return "Shiboken6"
    if "numpy" in relative:
        return "NumPy / OpenBLAS"
    if "cv2/" in relative:
        return "OpenCV"
    if name.startswith("libffi"):
        return "libffi (CPython dependency)"
    return "CPython"


def _copy_license_texts(destination: Path, root: Path) -> None:
    sources = {
        "python/LICENSE.txt": Path(sys.base_prefix) / "LICENSE.txt",
        "qt/LGPL-3.0.txt": root / "packaging/licenses/Qt-LGPL-3.0.txt",
        "qt/GPL-3.0.txt": root / "packaging/licenses/Qt-GPL-3.0.txt",
        "openssl/Apache-2.0.txt": root / "packaging/licenses/OpenSSL-Apache-2.0.txt",
    }
    for relative, source in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for package, subdir in (("numpy", "numpy"), ("opencv-python-headless", "opencv")):
        distribution = importlib.metadata.distribution(package)
        for relative in distribution.files or []:
            parts = relative.parts
            if not parts[0].endswith(".dist-info"):
                continue
            if "licenses" in parts:
                retained = Path(*parts[parts.index("licenses") + 1 :])
            elif relative.name.startswith("LICENSE"):
                retained = Path(relative.name)
            else:
                continue
            target = destination / subdir / retained
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(distribution.locate_file(relative), target)


def write_distribution_notices(distribution: Path, *, root: Path = ROOT) -> str:
    """Inventory the actual COLLECT output and include current license texts."""
    distribution, root = Path(distribution), Path(root)
    native = []
    versions: dict[str, set[str]] = {}
    for path in sorted((distribution / "_internal").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".dll", ".pyd"):
            continue
        relative = path.relative_to(distribution)
        component, version = _component(relative), _native_version(path)
        native.append(
            {
                "path": relative.as_posix(),
                "component": component,
                "version": version,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        if version:
            versions.setdefault(component, set()).add(version)
    if not native:
        raise ValueError("Cannot generate notices: no collected native runtime")
    wheels = {
        name: importlib.metadata.version(name)
        for name in (
            "PySide6-Essentials",
            "shiboken6",
            "numpy",
            "opencv-python-headless",
            "PyInstaller",
        )
    }
    sys.path.insert(0, str(root / "tools"))
    from fetch_model import PINNED

    models = []
    for model in PINNED:
        path = distribution / "_internal/models" / model.name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != model.sha256:
            raise ValueError(f"Model differs from pinned SHA-256: {model.name}")
        models.append({"path": path.relative_to(distribution).as_posix(), "sha256": digest})
    inventory = {"native": native, "wheels": wheels, "models": models}
    (distribution / "THIRD_PARTY_INVENTORY.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )
    rows = ["| Retained component | Observed version(s) |", "| --- | --- |"]
    rows += [f"| {name} | {', '.join(sorted(found))} |" for name, found in sorted(versions.items())]
    rows += [f"| Wheel/build tool: {name} | {version} |" for name, version in wheels.items()]
    template = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    start, end = template.index(START) + len(START), template.index(END)
    notice = template[:start] + "\n\n" + "\n".join(rows) + "\n\n" + template[end:]
    (distribution / "THIRD_PARTY_NOTICES.md").write_text(notice, encoding="utf-8")
    _copy_license_texts(distribution / "_internal/third_party_licenses", root)
    return notice


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--update-source", action="store_true")
    args = parser.parse_args()
    result = write_distribution_notices(args.dist)
    if args.update_source:
        (ROOT / "THIRD_PARTY_NOTICES.md").write_text(result, encoding="utf-8")
    print("Generated THIRD_PARTY_NOTICES.md, inventory and retained license texts")
