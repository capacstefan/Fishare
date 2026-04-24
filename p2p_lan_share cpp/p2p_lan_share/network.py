"""TLS networking: transfer server, client, queue, JSON-line protocol.

Protocol (all control messages are UTF-8 JSON lines ending with '\\n'):
  Sender -> Receiver (offer):
      {"type":"offer","kind":"files","from":"Name","from_id":"abc",
       "pin_required": true, "files":[{"name":"a.txt","size":123}, ...],
       "total_size": 12345}
      {"type":"offer","kind":"text","from":"Name","from_id":"abc","text":"..."}
      {"type":"offer","kind":"sync","from":"Name","from_id":"abc","folder":"FolderName"}

  Receiver -> Sender (response):
      {"type":"response","accept":true,"pin":"1234"}
      {"type":"response","accept":false,"reason":"rejected"}

  After accept on file transfer: sender streams raw bytes for each file in order
  (sizes taken from offer). Receiver closes when done.

  For sync, after accept, sender emits a stream of events:
      {"type":"sync_event","op":"put","path":"rel","size":N}  then N raw bytes
      {"type":"sync_event","op":"delete","path":"rel"}
      {"type":"sync_event","op":"stop"}
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

import p2plan_core  # native TLS file pump (required; no Python fallback)

from . import config, crypto_utils
from .util import unique_path


# =============================================================================
# Low-level helpers
# =============================================================================
def _send_json(sock: ssl.SSLSocket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


class LineReader:
    """Buffered reader that shares one internal buffer between JSON-line reads
    and raw-payload recv. This is the *only* read path for the protocol:

        reader = LineReader(sock)
        hdr = reader.read_json()
        data = reader.recv(n)       # drains buffer first, then sock.recv()
    """

    _BUF = 64 * 1024
    _MAX_LINE = 1 << 20  # 1 MB cap on a single control message

    def __init__(self, sock: ssl.SSLSocket) -> None:
        self._sock = sock
        self._buf = bytearray()

    def read_json(self) -> dict:
        while True:
            nl = self._buf.find(b"\n")
            if nl != -1:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                return json.loads(line.decode("utf-8"))
            if len(self._buf) > self._MAX_LINE:
                raise ValueError("control message too large")
            chunk = self._sock.recv(self._BUF)
            if not chunk:
                raise ConnectionError("socket closed")
            self._buf += chunk

    def recv(self, n: int) -> bytes:
        """Return up to n bytes, draining the buffer first."""
        if self._buf:
            take = min(n, len(self._buf))
            out = bytes(self._buf[:take])
            del self._buf[:take]
            return out
        return self._sock.recv(n)


def _human_eta(remaining: int, bps: float) -> str:
    if bps <= 0:
        return "--"
    s = int(remaining / bps)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _tune_sock(raw: socket.socket) -> None:
    """Raise buffer sizes and disable Nagle for better LAN throughput."""
    try:
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    except OSError:
        pass


# =============================================================================
# SSL contexts (self-signed, E2EE without identity verification â€” KISS)
# =============================================================================
def _server_ctx() -> ssl.SSLContext:
    cert, key = crypto_utils.ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


def _client_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# =============================================================================
# Data structures
# =============================================================================
@dataclass
class FileSpec:
    path: str       # absolute path (sender) or filename (receiver)
    size: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class IncomingOffer:
    """Represents a pending incoming transfer awaiting user decision."""
    kind: str                     # "files" | "text" | "sync"
    sender_name: str
    sender_id: str
    files: list[dict] = field(default_factory=list)
    total_size: int = 0
    pin_required: bool = False
    text: str = ""
    folder: str = ""
    _event: threading.Event = field(default_factory=threading.Event)
    _accept: bool = False
    _pin: str = ""

    def respond(self, accept: bool, pin: str = "") -> None:
        self._accept = accept
        self._pin = pin
        self._event.set()

    def wait(self, timeout: float = 120.0) -> tuple[bool, str]:
        self._event.wait(timeout=timeout)
        return self._accept, self._pin


# =============================================================================
# Receiver (TLS server)
# =============================================================================
class TransferServer(QObject):
    """Listens for incoming TLS connections. Emits signals for GUI to handle."""

    offer_received = pyqtSignal(object)
    file_progress = pyqtSignal(str, str, int, int, float, str)
    transfer_completed = pyqtSignal(str, list, int)
    recv_failed = pyqtSignal(str, str)         # sender_name, reason
    text_received = pyqtSignal(str, str)
    sync_started = pyqtSignal(str, str, object)
    impersonation_detected = pyqtSignal(str)   # claimed sender_name
    log = pyqtSignal(str)

    def __init__(self, get_state: Callable[[], dict]) -> None:
        """get_state() -> {'online':bool, 'muted':set[str], 'download_dir':str}."""
        super().__init__()
        self._get_state = get_state
        self._sock: socket.socket | None = None
        self._ctx: ssl.SSLContext | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._ctx = _server_ctx()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", config.TCP_PORT))
        self._sock.listen(16)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        assert self._sock and self._ctx
        while not self._stop.is_set():
            try:
                raw, addr = self._sock.accept()
            except OSError:
                return
            _tune_sock(raw)
            try:
                tls = self._ctx.wrap_socket(raw, server_side=True)
            except ssl.SSLError as e:
                self.log.emit(f"TLS handshake failed: {e}")
                raw.close()
                continue
            t = threading.Thread(target=self._handle_client, args=(tls, addr), daemon=True)
            t.start()

    def _handle_client(self, sock: ssl.SSLSocket, addr) -> None:
        try:
            sock.settimeout(config.SOCKET_TIMEOUT)
            reader = LineReader(sock)
            offer_msg = reader.read_json()
            if offer_msg.get("type") != "offer":
                _send_json(sock, {"type": "response", "accept": False, "reason": "bad protocol"})
                return

            state = self._get_state()
            sender_name = offer_msg.get("from", "Unknown")
            sender_id = offer_msg.get("from_id", "")
            kind = offer_msg.get("kind", "")

            # Impersonation check: if we already know a peer with this name but
            # under a *different* stable id (cert fingerprint), warn the user.
            # A renamed peer keeps its id; an impersonator has a fresh id.
            known_by_name = state.get("known_ids_by_name", {}).get(sender_name)
            if known_by_name and sender_id and known_by_name != sender_id:
                self.impersonation_detected.emit(sender_name)
                self.log.emit(
                    f"Impersonation warning: {sender_name!r} claims id={sender_id} "
                    f"but known id={known_by_name}"
                )

            # Auto-reject: offline or muted (mute is keyed on peer_id / fingerprint)
            if not state["online"]:
                _send_json(sock, {"type": "response", "accept": False, "reason": "offline"})
                return
            if sender_id in state["muted"]:
                _send_json(sock, {"type": "response", "accept": False, "reason": "muted"})
                return

            files = offer_msg.get("files", []) or []
            total_size = int(offer_msg.get("total_size", 0))

            # Receive-side size cap (prevents disk-fill DoS).
            if kind == "files":
                if total_size > config.MAX_FILE_SIZE or any(
                    int(f.get("size", 0)) > config.MAX_FILE_SIZE for f in files
                ):
                    _send_json(sock, {"type": "response", "accept": False, "reason": "too large"})
                    return

            offer = IncomingOffer(
                kind=kind,
                sender_name=sender_name,
                sender_id=sender_id,
                files=files,
                total_size=total_size,
                pin_required=bool(offer_msg.get("pin_required", False)),
                text="",  # body is delivered AFTER accept for text offers
                folder=offer_msg.get("folder", ""),
            )
            self.offer_received.emit(offer)
            accepted, pin = offer.wait(timeout=180.0)

            if not accepted:
                _send_json(sock, {"type": "response", "accept": False, "reason": "rejected"})
                return
            _send_json(sock, {"type": "response", "accept": True, "pin": pin})

            if kind == "text":
                # Body arrives as a separate JSON line now that accept has been sent.
                body = reader.read_json()
                self.text_received.emit(sender_name, str(body.get("text", "")))
            elif kind == "files":
                try:
                    self._recv_files(reader, offer, state["download_dir"])
                except Exception as e:
                    self.recv_failed.emit(sender_name, str(e))
                    raise
            elif kind == "sync":
                # Hand off the socket to sync manager via signal; don't close here.
                self.sync_started.emit(sender_name, offer.folder, sock)
                sock = None  # ownership transferred
        except Exception as e:
            self.log.emit(f"Receiver error from {addr}: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _recv_files(self, reader: LineReader, offer: IncomingOffer, download_dir: str) -> None:
        """Receive every file in the offer.

        For each file we hand the raw bytes-pump to the native p2plan_core
        module: it reads from `reader` and writes to disk inside a tight C++
        loop with the GIL released. Python stays responsible for the
        bookkeeping (per-task byte totals, progress signal formatting).
        """
        dest = Path(download_dir)
        dest.mkdir(parents=True, exist_ok=True)
        received_names: list[str] = []

        # Streaming: no control-message timeout; raw bytes only.
        reader._sock.settimeout(None)

        # Aggregate progress across the whole task (one bar per peer).
        total = sum(int(f.get("size", 0)) for f in offer.files)
        bytes_before = 0
        start = time.monotonic()

        def _emit(file_size: int, target_name: str) -> Callable[[int], None]:
            """Build a progress callback for the native core for one file."""
            def cb(done_in_file: int) -> None:
                done = bytes_before + done_in_file
                elapsed = max(time.monotonic() - start, 1e-6)
                bps = done / elapsed
                eta = _human_eta(total - done, bps)
                self.file_progress.emit(
                    offer.sender_name, target_name, done, total, bps, eta
                )
            return cb

        for fmeta in offer.files:
            name = os.path.basename(fmeta.get("name", "file"))
            size = int(fmeta.get("size", 0))
            target = unique_path(dest / name)
            received_names.append(target.name)

            p2plan_core.recv_file(reader, str(target), size, _emit(size, target.name))
            bytes_before += size

        self.transfer_completed.emit(offer.sender_name, received_names, bytes_before)


# =============================================================================
# Sender (TLS client) + transfer queue
# =============================================================================
class TransferTask(QObject):
    """Single-peer transfer job. Emits progress / done / failed."""

    progress = pyqtSignal(str, str, int, int, float, str)
    status = pyqtSignal(str, str)
    finished = pyqtSignal(str, bool, str)

    def __init__(
        self,
        peer_name: str,
        peer_addr: str,
        peer_port: int,
        kind: str,
        from_name: str,
        from_id: str,
        files: list[FileSpec] | None = None,
        text: str = "",
        pin: str = "",
    ) -> None:
        super().__init__()
        self.peer_name = peer_name
        self.peer_addr = peer_addr
        self.peer_port = peer_port
        self.kind = kind
        self.from_name = from_name
        self.from_id = from_id
        self.files = files or []
        self.text = text
        self.pin = pin  # empty means no PIN required

    def run(self) -> None:
        ctx = _client_ctx()
        try:
            raw = socket.create_connection((self.peer_addr, self.peer_port), timeout=10)
            _tune_sock(raw)
            sock = ctx.wrap_socket(raw, server_hostname=self.peer_addr)
        except Exception as e:
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, f"connect: {e}")
            return

        try:
            if self.kind == "files":
                offer = {
                    "type": "offer", "kind": "files",
                    "from": self.from_name, "from_id": self.from_id,
                    "pin_required": bool(self.pin),
                    "files": [{"name": f.name, "size": f.size} for f in self.files],
                    "total_size": sum(f.size for f in self.files),
                }
            elif self.kind == "text":
                offer = {
                    "type": "offer", "kind": "text",
                    "from": self.from_name, "from_id": self.from_id,
                }
            else:
                self.finished.emit(self.peer_name, False, "unknown kind")
                return

            self.status.emit(self.peer_name, "waiting_accept")
            _send_json(sock, offer)
            sock.settimeout(config.SOCKET_TIMEOUT * 6)  # allow user time to accept
            reader = LineReader(sock)
            resp = reader.read_json()
            if not resp.get("accept"):
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, resp.get("reason", "rejected"))
                return

            # Simple PIN compare — app is a LAN tool, TLS already protects transit.
            if self.pin and resp.get("pin", "") != self.pin:
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, "pin mismatch")
                return

            if self.kind == "text":
                # Deliver the body only after the receiver accepts.
                _send_json(sock, {"type": "text_body", "text": self.text})
                self.status.emit(self.peer_name, "done")
                self.finished.emit(self.peer_name, True, "")
                return

            # Stream files (aggregate progress across the whole task).
            self.status.emit(self.peer_name, "sending")
            sock.settimeout(None)
            total = sum(f.size for f in self.files)
            sent_before = 0
            start = time.monotonic()
            for f in self.files:
                self._send_file(sock, f, sent_before, total, start)
                sent_before += f.size
            self.status.emit(self.peer_name, "done")
            self.finished.emit(self.peer_name, True, "")
        except Exception as e:
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, str(e))
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _send_file(self, sock: ssl.SSLSocket, spec: FileSpec,
                   sent_before: int, total: int, start: float) -> None:
        """Send one file using the native core.

        The native loop reads from disk and writes to the SSL socket in C++,
        with the GIL released, so the Qt UI stays smooth even during large
        transfers. It invokes ``_on_progress`` roughly every 200 ms with the
        running byte count of this file; we translate that into the
        aggregate task-level ``progress`` signal.
        """
        def _on_progress(done_in_file: int) -> None:
            done = sent_before + done_in_file
            elapsed = max(time.monotonic() - start, 1e-6)
            bps = done / elapsed
            eta = _human_eta(total - done, bps)
            self.progress.emit(self.peer_name, spec.name, done, total, bps, eta)

        p2plan_core.send_file(sock, spec.path, _on_progress)


class TransferQueue(QObject):
    """Caps concurrent transfers to MAX_CONCURRENT_TRANSFERS."""

    def __init__(self, max_concurrent: int = config.MAX_CONCURRENT_TRANSFERS) -> None:
        super().__init__()
        self._sema = threading.Semaphore(max_concurrent)

    def submit(self, task: TransferTask) -> None:
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def _run(self, task: TransferTask) -> None:
        task.status.emit(task.peer_name, "queued")
        with self._sema:
            task.run()