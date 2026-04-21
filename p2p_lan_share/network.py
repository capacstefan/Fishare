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
  (sizes taken from offer). Receiver closes or replies {"type":"done"}.

  For sync, after accept, sender emits a stream of events:
      {"type":"sync_event","op":"add","path":"rel","size":N}  then N raw bytes
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
from queue import Queue
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from . import config, crypto_utils


# =============================================================================
# Low-level helpers
# =============================================================================
def _send_json(sock: ssl.SSLSocket, obj: dict) -> None:
    data = (json.dumps(obj) + "\n").encode("utf-8")
    sock.sendall(data)


def _recv_line(sock: ssl.SSLSocket, max_bytes: int = 1 << 20) -> str:
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("socket closed")
        if ch == b"\n":
            break
        buf += ch
        if len(buf) > max_bytes:
            raise ValueError("control message too large")
    return buf.decode("utf-8")


def _recv_json(sock: ssl.SSLSocket) -> dict:
    return json.loads(_recv_line(sock))


def _recv_exact(sock: ssl.SSLSocket, n: int, progress: Callable[[int], None] | None = None) -> None:
    """Discard-free: caller-provided progress is invoked; bytes themselves handled elsewhere."""
    raise NotImplementedError  # receive loops below handle raw reads


def _pick_chunk(size: int) -> int:
    return config.CHUNK_LARGE if size >= config.LARGE_FILE_THRESHOLD else config.CHUNK_SMALL


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


# =============================================================================
# SSL contexts (self-signed, E2EE without identity verification — KISS)
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

    # Offer awaits user decision. GUI connects, inspects, calls offer.respond().
    offer_received = pyqtSignal(object)             # IncomingOffer

    file_progress = pyqtSignal(str, str, int, int, float, str)
    # sender_name, filename, done_bytes, total_bytes, speed_bps, eta

    transfer_completed = pyqtSignal(str, list, int)
    # sender_name, [filenames], total_bytes

    text_received = pyqtSignal(str, str)            # sender_name, text
    sync_started = pyqtSignal(str, str, object)     # sender_name, folder, socket-handle
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
            offer_msg = _recv_json(sock)
            if offer_msg.get("type") != "offer":
                _send_json(sock, {"type": "response", "accept": False, "reason": "bad protocol"})
                return

            state = self._get_state()
            sender_name = offer_msg.get("from", "Unknown")
            sender_id = offer_msg.get("from_id", "")
            kind = offer_msg.get("kind", "")

            # Auto-reject: offline or muted
            if not state["online"]:
                _send_json(sock, {"type": "response", "accept": False, "reason": "offline"})
                return
            if sender_name in state["muted"]:
                _send_json(sock, {"type": "response", "accept": False, "reason": "muted"})
                return

            offer = IncomingOffer(
                kind=kind,
                sender_name=sender_name,
                sender_id=sender_id,
                files=offer_msg.get("files", []),
                total_size=int(offer_msg.get("total_size", 0)),
                pin_required=bool(offer_msg.get("pin_required", False)),
                text=offer_msg.get("text", ""),
                folder=offer_msg.get("folder", ""),
            )
            self.offer_received.emit(offer)
            accepted, pin = offer.wait(timeout=180.0)

            if not accepted:
                _send_json(sock, {"type": "response", "accept": False, "reason": "rejected"})
                return
            _send_json(sock, {"type": "response", "accept": True, "pin": pin})

            if kind == "text":
                self.text_received.emit(sender_name, offer.text)
                return
            if kind == "files":
                self._recv_files(sock, offer, state["download_dir"])
                return
            if kind == "sync":
                # Hand off the socket to sync manager via signal; don't close here.
                self.sync_started.emit(sender_name, offer.folder, sock)
                return  # ownership transferred; finally will try close — guard below
        except Exception as e:
            self.log.emit(f"Receiver error from {addr}: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _recv_files(self, sock: ssl.SSLSocket, offer: IncomingOffer, download_dir: str) -> None:
        dest = Path(download_dir)
        dest.mkdir(parents=True, exist_ok=True)
        received_names: list[str] = []
        total_done = 0
        for fmeta in offer.files:
            name = os.path.basename(fmeta.get("name", "file"))
            size = int(fmeta.get("size", 0))
            target = _unique_path(dest / name)
            received_names.append(target.name)

            chunk = _pick_chunk(size)
            done = 0
            start = time.monotonic()
            last_emit = start
            sock.settimeout(None)
            with target.open("wb") as f:
                while done < size:
                    to_read = min(chunk, size - done)
                    data = sock.recv(to_read)
                    if not data:
                        raise ConnectionError("connection lost during file receive")
                    f.write(data)
                    done += len(data)
                    total_done += len(data)
                    now = time.monotonic()
                    if now - last_emit >= 0.1 or done == size:
                        elapsed = max(now - start, 1e-6)
                        bps = done / elapsed
                        eta = _human_eta(size - done, bps)
                        self.file_progress.emit(
                            offer.sender_name, target.name, done, size, bps, eta
                        )
                        last_emit = now
        self.transfer_completed.emit(offer.sender_name, received_names, total_done)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    i = 1
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


# =============================================================================
# Sender (TLS client) + transfer queue
# =============================================================================
class TransferTask(QObject):
    """Single-peer transfer job. Emits progress / done / failed."""

    progress = pyqtSignal(str, str, int, int, float, str)
    # peer_name, filename, done, total, bps, eta
    status = pyqtSignal(str, str)   # peer_name, status_text ("queued","sending","done","failed","rejected","waiting_pin")
    finished = pyqtSignal(str, bool, str)  # peer_name, success, reason

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
                    "text": self.text,
                }
            else:
                self.finished.emit(self.peer_name, False, "unknown kind")
                return

            self.status.emit(self.peer_name, "waiting_accept")
            _send_json(sock, offer)
            sock.settimeout(config.SOCKET_TIMEOUT * 6)  # allow user time to accept
            resp = _recv_json(sock)
            if not resp.get("accept"):
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, resp.get("reason", "rejected"))
                return

            if self.pin and resp.get("pin", "") != self.pin:
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, "pin mismatch")
                return

            if self.kind == "text":
                self.status.emit(self.peer_name, "done")
                self.finished.emit(self.peer_name, True, "")
                return

            # Stream files
            self.status.emit(self.peer_name, "sending")
            sock.settimeout(None)
            for f in self.files:
                self._send_file(sock, f)
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

    def _send_file(self, sock: ssl.SSLSocket, spec: FileSpec) -> None:
        chunk = _pick_chunk(spec.size)
        done = 0
        start = time.monotonic()
        last_emit = start
        with open(spec.path, "rb") as f:
            while True:
                buf = f.read(chunk)
                if not buf:
                    break
                sock.sendall(buf)
                done += len(buf)
                now = time.monotonic()
                if now - last_emit >= 0.1 or done == spec.size:
                    elapsed = max(now - start, 1e-6)
                    bps = done / elapsed
                    eta = _human_eta(spec.size - done, bps)
                    self.progress.emit(self.peer_name, spec.name, done, spec.size, bps, eta)
                    last_emit = now


class TransferQueue(QObject):
    """Caps concurrent transfers to MAX_CONCURRENT_TRANSFERS (default 4)."""

    task_started = pyqtSignal(str)   # peer_name
    task_queued = pyqtSignal(str)    # peer_name

    def __init__(self, max_concurrent: int = config.MAX_CONCURRENT_TRANSFERS) -> None:
        super().__init__()
        self._sema = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._active = 0

    def submit(self, task: TransferTask) -> None:
        self.task_queued.emit(task.peer_name)
        t = threading.Thread(target=self._run, args=(task,), daemon=True)
        t.start()

    def _run(self, task: TransferTask) -> None:
        task.status.emit(task.peer_name, "queued")
        with self._sema:
            with self._lock:
                self._active += 1
            try:
                self.task_started.emit(task.peer_name)
                task.run()
            finally:
                with self._lock:
                    self._active -= 1
