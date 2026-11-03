"""Windows DPAPI (current user): only this Windows account can decrypt the data."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "Desk Sentry people"


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte)))


def available() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _call(name: str, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    func = getattr(crypt32, name)
    blob_in, buffer = _blob(data)
    blob_out = _Blob()
    try:
        if name == "CryptProtectData":
            ok = func(
                ctypes.byref(blob_in),
                _DESCRIPTION,
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(blob_out),
            )
        else:
            ok = func(
                ctypes.byref(blob_in),
                None,
                None,
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(blob_out),
            )
        if not ok:
            raise OSError(ctypes.get_last_error(), f"{name} failed")
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.memset(buffer, 0, ctypes.sizeof(buffer))
        if blob_out.pbData:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, wintypes.HLOCAL))


def protect(plaintext: bytes) -> bytes:
    if not available():
        raise OSError("DPAPI requires Windows")
    return _call("CryptProtectData", plaintext)


def unprotect(ciphertext: bytes) -> bytes:
    if not available():
        raise OSError("DPAPI requires Windows")
    return _call("CryptUnprotectData", ciphertext)
