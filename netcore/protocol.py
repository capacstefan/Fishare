import json
import struct
import socket
import logging

LOG = logging.getLogger(__name__)

HEADER_LEN = 4  # 4 bytes big-endian message length prefix


class Proto:
    """Encapsulates send/receive logic for framed JSON messages."""

    @staticmethod
    def send_json(sock: socket.socket, obj: dict, aead=None):
        """Send a JSON object with optional AEAD encryption."""
        data = json.dumps(obj).encode("utf-8")
        if aead:
            data = aead.encrypt(data)
        length = struct.pack(">I", len(data))
        sock.sendall(length + data)

    @staticmethod
    def recv_json(sock: socket.socket, aead=None) -> dict:
        """Receive and decode a JSON object."""
        header = Proto._recvall(sock, HEADER_LEN)
        if not header:
            raise ConnectionError("peer closed")
        (length,) = struct.unpack(">I", header)
        data = Proto._recvall(sock, length)
        if aead:
            data = aead.decrypt(data)
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def _recvall(sock: socket.socket, n: int) -> bytes:
        """Receive exactly n bytes."""
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf
