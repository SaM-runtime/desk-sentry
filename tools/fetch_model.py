"""Get the pinned models (explicit, one-off step; the app never downloads anything).

Keep verified local models; otherwise download and verify their SHA-256.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"


@dataclass(frozen=True)
class Model:
    name: str
    sha256: str
    urls: tuple[str, ...]


ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/refs/heads/main/models"
PINNED = (
    Model(
        "object_detection_nanodet_2022nov.onnx",
        "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186",
        (
            "https://huggingface.co/opencv/object_detection_nanodet/resolve/"
            "5bfd47077350a726ad440dd7bd1e1e35e8ebcfb2/object_detection_nanodet_2022nov.onnx",
        ),
    ),
    Model(
        "face_detection_yunet_2026may.onnx",
        "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
        (f"{ZOO}/face_detection_yunet/face_detection_yunet_2026may.onnx",),
    ),
    Model(
        "face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        (
            "https://huggingface.co/opencv/face_recognition_sface/resolve/"
            "3d7082438a6e4551e840c9b2bb60b71e8da4b524/face_recognition_sface_2021dec.onnx",
            f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        ),
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure(model: Model) -> bool:
    target = MODELS / model.name
    if target.is_file() and sha256(target) == model.sha256:
        print(f"ok        {model.name}")
        return True
    MODELS.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    for url in model.urls:
        try:
            print(f"download  {url}")
            urllib.request.urlretrieve(url, partial)
        except OSError as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            continue
        if sha256(partial) == model.sha256:
            partial.replace(target)
            print(f"ok        {model.name}")
            return True
        partial.unlink()
        print("  checksum mismatch", file=sys.stderr)
    return False


def main() -> int:
    return 0 if all(ensure(m) for m in PINNED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
