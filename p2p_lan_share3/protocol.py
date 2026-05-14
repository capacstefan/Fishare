"""Length-prefixed wire protocol over TLS.

Frame: [1-byte type][4-byte BE length][payload]
  'J' = JSON utf-8     'D' = raw data
"""
from __future__ import annotations

import json
import socket
import ssl
import struct
import threading

FT_JSON = b"J"
FT_DATA = b"D"
_HEADER = struct.Struct(">cI")
HEADER_LEN = _HEADER.size
MAX_FRAME = 4 * 1024 * 1024


class WireError(Exception):
    pass


class Wire:
    """Framed reader/writer around a TLS socket. Sends are mutex-protected."""

    __slots__ = ("_s", "_send_lock", "_buf")

    def __init__(self, sock: ssl.SSLSocket) -> None:
        self._s = sock
        self._send_lock = threading.Lock()
        self._buf = bytearray()

    # ---- send ----
    def send_json(self, obj: dict) -> None:
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send(FT_JSON, body)

    def send_data(self, payload: bytes) -> None:
        if len(payload) > MAX_FRAME:
            raise WireError(f"data frame too large: {len(payload)}")
        self._send(FT_DATA, payload)

    def _send(self, ftype: bytes, payload: bytes) -> None:
        head = _HEADER.pack(ftype, len(payload))
        with self._send_lock:
            self._s.sendall(head)
            if payload:
                self._s.sendall(payload)

    # ---- recv ----
    def recv_frame(self) -> tuple[bytes, bytes]:
        head = self._recv_exact(HEADER_LEN)
        ftype, length = _HEADER.unpack(head)
        if length > MAX_FRAME:
            raise WireError(f"frame too large: {length}")
        return ftype, (self._recv_exact(length) if length else b"")

    def recv_json(self) -> dict:
        ftype, payload = self.recv_frame()
        if ftype != FT_JSON:
            raise WireError(f"expected JSON, got {ftype!r}")
        return json.loads(payload.decode("utf-8"))

    def _recv_exact(self, n: int) -> bytes:
        buf = self._buf
        while len(buf) < n:
            chunk = self._s.recv(max(n - len(buf), 65536))
            if not chunk:
                raise ConnectionError("socket closed")
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    def settimeout(self, t: float | None) -> None:
        self._s.settimeout(t)

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


# ---- TLS contexts (LAN tool — no peer identity verification) -------------
def server_ctx() -> ssl.SSLContext:
    from . import crypto_utils
    cert, key = crypto_utils.ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


def client_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def tune(raw: socket.socket) -> None:
    """Disable Nagle, raise socket buffers for LAN throughput."""
    try:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    except OSError:
        pass


def connect(addr: str, port: int) -> Wire:
    raw = socket.create_connection((addr, port), timeout=10)
    tune(raw)
    return Wire(client_ctx().wrap_socket(raw, server_hostname=addr))


def open_offer(addr: str, port: int, offer: dict, accept_timeout: float = 180.0) -> Wire:
    """Connect, send offer, wait for accept. Raises RuntimeError on rejection."""
    w = connect(addr, port)
    try:
        w.send_json(offer)
        w.settimeout(accept_timeout)
        resp = w.recv_json()
    except Exception:
        w.close()
        raise
    if not resp.get("accept"):
        w.close()
        raise RuntimeError(resp.get("reason", "rejected"))
    w.settimeout(None)
    return w
