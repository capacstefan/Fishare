"""Tests for native.sha256_streaming — must match stdlib hashlib bit-for-bit."""
from __future__ import annotations

import hashlib

import pytest

try:
    from p2p_lan_share import native
    _HAVE_NATIVE = True
    _NATIVE_ERR = None
except Exception as e:  # pragma: no cover
    _HAVE_NATIVE = False
    _NATIVE_ERR = str(e)


pytestmark = pytest.mark.skipif(
    not _HAVE_NATIVE, reason=f"p2p_native.dll not loadable: {_NATIVE_ERR}"
)


def _ref(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestNativeSha:
    def test_empty(self):
        h = native.sha256_streaming()
        assert h.hexdigest() == _ref(b"")

    def test_single_update(self):
        h = native.sha256_streaming()
        h.update(b"hello world")
        assert h.hexdigest() == _ref(b"hello world")

    def test_multi_update_matches_concatenated(self):
        chunks = [b"abc", b"def", b"123456789", b"\x00\xff" * 1024]
        h = native.sha256_streaming()
        for c in chunks:
            h.update(c)
        assert h.hexdigest() == _ref(b"".join(chunks))

    def test_large_input(self):
        data = b"A" * (4 * 1024 * 1024 + 17)
        h = native.sha256_streaming()
        h.update(data)
        assert h.hexdigest() == _ref(data)

    def test_double_finalize_raises(self):
        h = native.sha256_streaming()
        h.update(b"x")
        h.hexdigest()
        with pytest.raises(RuntimeError):
            h.hexdigest()
