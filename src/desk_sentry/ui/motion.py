"""Windows client-area animation preference shared by all presentation surfaces."""

from __future__ import annotations

import ctypes
import sys


def animations_enabled() -> bool:
    """Read SPI_GETCLIENTAREAANIMATION without modifying the system preference."""
    if sys.platform != "win32":
        return True
    enabled = ctypes.c_int(1)
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    except (AttributeError, OSError):
        return True
    return bool(enabled.value) if ok else True
