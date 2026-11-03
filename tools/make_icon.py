"""Generate the project's original MIT rear-view mirror artwork; stdlib only."""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 32, 48, 256)


def pixel(x: float, y: float) -> tuple[int, int, int, int]:
    # Light rim + dark glass stay visible on either desktop background.
    if 0.44 <= x <= 0.56 and 0.55 <= y <= 0.85:
        return (184, 206, 217, 255)
    dx = max(abs(x - 0.5) - 0.29, 0)
    dy = max(abs(y - 0.43) - 0.10, 0)
    radius = dx * dx + dy * dy
    if radius <= 0.12**2:
        if radius > 0.085**2:
            return (222, 239, 245, 255)
        if (x - 0.67) ** 2 + (y - 0.43) ** 2 < 0.065**2:
            return (61, 214, 181, 255)
        if 0.25 < x < 0.43 and abs(y - (0.63 - x * 0.55)) < 0.018:
            return (117, 152, 169, 255)
        return (28, 49, 64, 255)
    return (0, 0, 0, 0)


def png(size: int) -> bytes:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            samples = [
                pixel((x + (a + 0.5) / 4) / size, (y + (b + 0.5) / 4) / size)
                for a in range(4)
                for b in range(4)
            ]
            alpha = sum(p[3] for p in samples)
            raw.extend(
                round(sum(p[c] * p[3] for p in samples) / alpha) if alpha else 0 for c in range(3)
            )
            raw.append(round(alpha / 16))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    frames = [png(size) for size in SIZES]
    offset = 6 + 16 * len(frames)
    entries = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    for size, frame in zip(SIZES, frames, strict=True):
        entries.extend(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset)
        )
        offset += len(frame)
    (ROOT / "packaging/desk_sentry.ico").write_bytes(bytes(entries) + b"".join(frames))
    assets = ROOT / "src/desk_sentry/assets"
    assets.mkdir(exist_ok=True)
    (assets / "desk_sentry.png").write_bytes(frames[-1])


if __name__ == "__main__":
    main()
