"""Bridge to p2p_native.dll (streaming SHA-256, GIL released per call)."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_DLL_NAME = "p2p_native.dll" if sys.platform == "win32" else "libp2p_native.so"


def _load() -> ctypes.CDLL:
    here = Path(__file__).resolve().parent
    for cand in (here / _DLL_NAME, here.parent / "native" / _DLL_NAME, Path(_DLL_NAME)):
        try:
            return ctypes.CDLL(str(cand))
        except OSError:
            continue
    raise RuntimeError(
        f"{_DLL_NAME} not found. Build it first by running: py native/build.py"
    )


_LIB = _load()
_LIB.p2p_sha256_new.restype = ctypes.c_void_p
_LIB.p2p_sha256_new.argtypes = []
_LIB.p2p_sha256_update.restype = None
_LIB.p2p_sha256_update.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_LIB.p2p_sha256_final.restype = None
_LIB.p2p_sha256_final.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_LIB.p2p_sha256_free.restype = None
_LIB.p2p_sha256_free.argtypes = [ctypes.c_void_p]


class _NativeSha:
    __slots__ = ("_h",)

    def __init__(self) -> None:
        h = _LIB.p2p_sha256_new()
        if not h:
            raise MemoryError("p2p_sha256_new failed")
        self._h = h

    def update(self, data: bytes) -> None:
        if data and self._h is not None:
            _LIB.p2p_sha256_update(self._h, bytes(data), len(data))

    def hexdigest(self) -> str:
        if self._h is None:
            raise RuntimeError("hasher already finalized")
        out = ctypes.create_string_buffer(32)
        _LIB.p2p_sha256_final(self._h, out)
        digest = bytes(out.raw[:32]).hex()
        _LIB.p2p_sha256_free(self._h)
        self._h = None
        return digest

    def __del__(self) -> None:
        h = getattr(self, "_h", None)
        if h:
            try:
                _LIB.p2p_sha256_free(h)
            except Exception:
                pass


def sha256_streaming() -> _NativeSha:
    """Return an incremental SHA-256 hasher (.update / .hexdigest)."""
    return _NativeSha()
