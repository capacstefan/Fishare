"""Tests for the length-prefixed Wire protocol.

The Wire class only calls ``sendall`` / ``recv`` on the underlying socket,
so we can exercise it end-to-end with a plain TCP socketpair (no TLS).
"""
from __future__ import annotations

import socket
import threading

import pytest

from p2p_lan_share import protocol
from p2p_lan_share.protocol import (
    FT_DATA, FT_JSON, MAX_FRAME, Wire, WireError,
)


def _pair() -> tuple[Wire, Wire]:
    a, b = socket.socketpair()
    return Wire(a), Wire(b)


class TestWireFraming:
    def test_send_and_recv_json(self):
        w1, w2 = _pair()
        try:
            w1.send_json({"hello": "world", "n": 42})
            assert w2.recv_json() == {"hello": "world", "n": 42}
        finally:
            w1.close(); w2.close()

    def test_send_and_recv_data(self):
        w1, w2 = _pair()
        try:
            payload = b"\x00\x01\x02" * 5000
            w1.send_data(payload)
            ftype, body = w2.recv_frame()
            assert ftype == FT_DATA
            assert body == payload
        finally:
            w1.close(); w2.close()

    def test_empty_data_frame(self):
        w1, w2 = _pair()
        try:
            w1.send_data(b"")
            ftype, body = w2.recv_frame()
            assert ftype == FT_DATA and body == b""
        finally:
            w1.close(); w2.close()

    def test_recv_json_wrong_type_raises(self):
        w1, w2 = _pair()
        try:
            w1.send_data(b"raw")
            with pytest.raises(WireError):
                w2.recv_json()
        finally:
            w1.close(); w2.close()

    def test_send_data_too_large_raises(self):
        w1, w2 = _pair()
        try:
            with pytest.raises(WireError):
                w1.send_data(b"x" * (MAX_FRAME + 1))
        finally:
            w1.close(); w2.close()

    def test_closed_socket_raises_connection_error(self):
        w1, w2 = _pair()
        w1.close()
        with pytest.raises((ConnectionError, OSError)):
            w2.recv_json()
        w2.close()

    def test_interleaved_json_and_data(self):
        w1, w2 = _pair()
        try:
            w1.send_json({"a": 1})
            w1.send_data(b"AAA")
            w1.send_json({"b": 2})
            assert w2.recv_json() == {"a": 1}
            ftype, body = w2.recv_frame()
            assert ftype == FT_DATA and body == b"AAA"
            assert w2.recv_json() == {"b": 2}
        finally:
            w1.close(); w2.close()

    def test_send_lock_serialises_concurrent_writes(self):
        """Two threads sending big frames concurrently must not corrupt the stream."""
        w1, w2 = _pair()
        N = 50
        a = b"A" * 1024
        b = b"B" * 1024

        def push(payload):
            for _ in range(N):
                w1.send_data(payload)

        t1 = threading.Thread(target=push, args=(a,))
        t2 = threading.Thread(target=push, args=(b,))
        t1.start(); t2.start()
        try:
            seen_a = seen_b = 0
            for _ in range(2 * N):
                ftype, body = w2.recv_frame()
                assert ftype == FT_DATA
                # Each frame must be exactly one of the originals, never mixed.
                if body == a:
                    seen_a += 1
                elif body == b:
                    seen_b += 1
                else:
                    pytest.fail("frame corruption: payload mixed across threads")
            assert seen_a == N and seen_b == N
        finally:
            t1.join(); t2.join()
            w1.close(); w2.close()


class TestTuneAndContexts:
    def test_server_ctx_loads_cert(self):
        ctx = protocol.server_ctx()
        assert ctx is not None

    def test_client_ctx_skips_verification(self):
        ctx = protocol.client_ctx()
        assert ctx.check_hostname is False
        import ssl
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_tune_does_not_raise_on_real_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            protocol.tune(s)  # must not raise
        finally:
            s.close()
