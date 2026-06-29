"""TLS file/text transfer: server, sender task, outbound queue.

Wire protocol (after offer/response accept):
    files: file_begin -> N data frames -> file_end(sha256) -> ... -> all_done
    text:  text_body
    sync:  Wire is handed to sync.py
"""
from __future__ import annotations

import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from . import config, native, protocol, storage
from .protocol import FT_DATA, MAX_FRAME, Wire, WireError
from .util import fmt_eta, unique_path

_CHUNK = min(config.CHUNK, MAX_FRAME)
_PROGRESS_INTERVAL = 0.1  # seconds between UI progress emissions


class TransferCancelled(Exception):
    """Raised internally when a transfer is cancelled by the user."""


@dataclass
class FileSpec:
    path: str
    size: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def exceeds_file_size_limit(
    sizes: list[int], *, declared_total: int | None = None,
) -> bool:
    """True if any file or the batch total exceeds MAX_FILE_SIZE."""
    if any(s > config.MAX_FILE_SIZE for s in sizes):
        return True
    if sum(sizes) > config.MAX_FILE_SIZE:
        return True
    if declared_total is not None and declared_total > config.MAX_FILE_SIZE:
        return True
    return False


@dataclass
class IncomingOffer:
    kind: str
    sender_name: str
    sender_id: str
    files: list = field(default_factory=list)
    total_size: int = 0
    pin_required: bool = False
    folder: str = ""
    _event: threading.Event = field(default_factory=threading.Event)
    _accept: bool = False
    _pin: str = ""

    def respond(self, accept: bool, pin: str = "") -> None:
        self._accept = accept
        self._pin = pin
        self._event.set()

    def wait(self, timeout: float = 180.0) -> tuple[bool, str]:
        self._event.wait(timeout=timeout)
        return self._accept, self._pin


# =============================================================================
# Server (receiver)
# =============================================================================
class TransferServer(QObject):
    offer_received = pyqtSignal(object)
    file_progress = pyqtSignal(str, str, int, int, float, str)
    transfer_completed = pyqtSignal(str, list, int)
    recv_failed = pyqtSignal(str, str)
    recv_cancelled = pyqtSignal(str)
    text_received = pyqtSignal(str, str)
    sync_started = pyqtSignal(str, str, object)  # name, folder, Wire
    log = pyqtSignal(str)

    def __init__(self, get_state: Callable[[], dict]) -> None:
        super().__init__()
        self._get_state = get_state
        self._sock: socket.socket | None = None
        self._ctx: ssl.SSLContext | None = None
        self._stop = threading.Event()
        # Active inbound file transfers, keyed by sender display name.
        self._active_recv: dict[str, Wire] = {}
        self._cancelled_recv: set[str] = set()
        self._recv_lock = threading.Lock()

    # ---- public cancellation API -----------------------------------
    def cancel_recv(self, sender_name: str) -> bool:
        """Abort an inbound file transfer from `sender_name`. Idempotent.

        Returns True if a transfer was actively cancelled.
        """
        with self._recv_lock:
            wire = self._active_recv.pop(sender_name, None)
            if wire is None:
                return False
            self._cancelled_recv.add(sender_name)
        try:
            wire.close()
        except Exception:
            pass
        return True

    def start(self) -> None:
        self._ctx = protocol.server_ctx()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", config.TCP_PORT))
        self._sock.listen(16)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw, addr = self._sock.accept()
            except OSError:
                return
            protocol.tune(raw)
            try:
                tls = self._ctx.wrap_socket(raw, server_side=True)
            except ssl.SSLError as e:
                self.log.emit(f"TLS handshake failed: {e}")
                raw.close()
                continue
            threading.Thread(target=self._handle, args=(tls, addr), daemon=True).start()

    def _handle(self, sock: ssl.SSLSocket, addr) -> None:
        wire = Wire(sock)
        keep_open = False
        try:
            sock.settimeout(config.SOCKET_TIMEOUT)
            msg = wire.recv_json()
            if msg.get("type") != "offer":
                wire.send_json({"type": "response", "accept": False, "reason": "bad protocol"})
                return

            state = self._get_state()
            sender = msg.get("from", "Unknown")
            sender_id = msg.get("from_id", "")
            kind = msg.get("kind", "")

            if not state["online"]:
                wire.send_json({"type": "response", "accept": False, "reason": "offline"})
                return
            if sender_id in state["muted"]:
                wire.send_json({"type": "response", "accept": False, "reason": "muted"})
                return

            peer_fp = wire.peer_fingerprint()
            if peer_fp and sender_id:
                if peer_fp[:len(sender_id)] != sender_id:
                    wire.send_json({"type": "response", "accept": False,
                                    "reason": "identity mismatch"})
                    return
                ok, reason = storage.check_and_pin(sender_id, peer_fp)
                if not ok:
                    wire.send_json({"type": "response", "accept": False,
                                    "reason": reason or "pinned fingerprint mismatch"})
                    return

            files = msg.get("files", []) or []
            total = int(msg.get("total_size", 0))
            sizes = [int(f.get("size", 0)) for f in files]
            if kind == "files" and exceeds_file_size_limit(sizes, declared_total=total):
                wire.send_json({"type": "response", "accept": False, "reason": "too large"})
                return

            offer = IncomingOffer(
                kind=kind, sender_name=sender, sender_id=sender_id,
                files=files, total_size=total,
                pin_required=bool(msg.get("pin_required", False)),
                folder=msg.get("folder", ""),
            )
            self.offer_received.emit(offer)
            accepted, pin = offer.wait(timeout=180.0)
            if not accepted:
                wire.send_json({"type": "response", "accept": False, "reason": "rejected"})
                return
            wire.send_json({"type": "response", "accept": True, "pin": pin})
            sock.settimeout(None)

            if kind == "text":
                body = wire.recv_json()
                self.text_received.emit(sender, str(body.get("text", "")))
            elif kind == "files":
                with self._recv_lock:
                    self._active_recv[sender] = wire
                try:
                    self._recv_files(wire, offer, state["download_dir"])
                except Exception as e:
                    if self._consume_cancel(sender):
                        self.recv_cancelled.emit(sender)
                    else:
                        self.recv_failed.emit(sender, str(e))
                    raise
                finally:
                    with self._recv_lock:
                        self._active_recv.pop(sender, None)
            elif kind == "sync":
                # Hand the Wire to sync subsystem — do NOT close here.
                self.sync_started.emit(sender, offer.folder, wire)
                keep_open = True
        except Exception as e:
            self.log.emit(f"Receiver error from {addr}: {e}")
        finally:
            if not keep_open:
                wire.close()

    def _consume_cancel(self, sender_name: str) -> bool:
        with self._recv_lock:
            if sender_name not in self._cancelled_recv:
                return False
            self._cancelled_recv.discard(sender_name)
            return True

    def _recv_files(self, wire: Wire, offer: IncomingOffer, download_dir: str) -> None:
        dest = Path(download_dir)
        dest.mkdir(parents=True, exist_ok=True)
        total = sum(int(f.get("size", 0)) for f in offer.files)
        done = 0
        start = time.monotonic()
        last = 0.0  # ensures the first chunk always emits
        names: list[str] = []

        for _ in offer.files:
            hdr = wire.recv_json()
            if hdr.get("type") != "file_begin":
                raise WireError(f"unexpected frame: {hdr.get('type')}")
            name = os.path.basename(hdr.get("name", "file"))
            size = int(hdr.get("size", 0))
            part = dest / (name + ".part")
            part.unlink(missing_ok=True)

            hasher = native.sha256_streaming()
            written = 0
            with part.open("wb", buffering=1 << 20) as f:
                while written < size:
                    ftype, payload = wire.recv_frame()
                    if ftype != FT_DATA or not payload:
                        raise WireError("expected data frame")
                    if written + len(payload) > size:
                        raise WireError("data overflow")
                    f.write(payload)
                    hasher.update(payload)
                    written += len(payload)
                    done += len(payload)
                    now = time.monotonic()
                    if now - last >= _PROGRESS_INTERVAL or done == total:
                        capped = min(done, total)
                        bps = capped / max(now - start, 1e-6)
                        self.file_progress.emit(
                            offer.sender_name, name, capped, total, bps,
                            fmt_eta(max(total - capped, 0), bps),
                        )
                        last = now

            tail = wire.recv_json()
            if tail.get("type") != "file_end" or tail.get("sha256") != hasher.hexdigest():
                part.unlink(missing_ok=True)
                raise WireError("integrity check failed")

            final = unique_path(dest / name)
            part.replace(final)
            names.append(final.name)

        end = wire.recv_json()
        if end.get("type") != "all_done":
            raise WireError("missing all_done")
        self.transfer_completed.emit(offer.sender_name, names, done)


# =============================================================================
# Sender task + queue
# =============================================================================
class TransferTask(QObject):
    progress = pyqtSignal(str, str, int, int, float, str)
    status = pyqtSignal(str, str)
    finished = pyqtSignal(str, bool, str)

    def __init__(self, peer_name, peer_addr, peer_port, kind,
                 from_name, from_id, peer_id="", files=None, text="", pin="") -> None:
        super().__init__()
        self.peer_name = peer_name
        self.peer_addr = peer_addr
        self.peer_port = peer_port
        self.kind = kind
        self.from_name = from_name
        self.from_id = from_id
        self.peer_id = peer_id or ""
        self.files = files or []
        self.text = text
        self.pin = pin
        self._cancel = threading.Event()
        self._wire: Wire | None = None

    # ---- public cancellation API -----------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> bool:
        """Request cancellation. Idempotent; safe to call from any thread.

        Returns True the first time it transitions from active to cancelled.
        """
        if self._cancel.is_set():
            return False
        self._cancel.set()
        wire = self._wire
        if wire is not None:
            try:
                wire.close()
            except Exception:
                pass
        return True

    def _offer(self) -> dict | None:
        if self.kind == "files":
            return {
                "type": "offer", "kind": "files",
                "from": self.from_name, "from_id": self.from_id,
                "pin_required": bool(self.pin),
                "files": [{"name": f.name, "size": f.size} for f in self.files],
                "total_size": sum(f.size for f in self.files),
            }
        if self.kind == "text":
            return {"type": "offer", "kind": "text",
                    "from": self.from_name, "from_id": self.from_id}
        return None

    def _verify_peer(self, wire: Wire) -> tuple[bool, str]:
        if not self.peer_id:
            return True, ""
        fp = wire.peer_fingerprint()
        if not fp:
            return False, "peer certificate missing"
        if fp[:len(self.peer_id)] != self.peer_id:
            return False, "peer identity mismatch"
        ok, reason = storage.check_and_pin(self.peer_id, fp)
        if not ok:
            return False, reason or "pinned fingerprint mismatch"
        return True, ""

    def run(self) -> None:
        offer = self._offer()
        if offer is None:
            self.finished.emit(self.peer_name, False, "unknown kind")
            return
        if self._cancel.is_set():
            self._emit_cancelled()
            return
        try:
            wire = protocol.connect(self.peer_addr, self.peer_port)
        except Exception as e:
            if self._cancel.is_set():
                self._emit_cancelled()
                return
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, f"connect: {e}")
            return

        self._wire = wire
        if self._cancel.is_set():
            wire.close()
            self._emit_cancelled()
            return

        ok, reason = self._verify_peer(wire)
        if not ok:
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, reason)
            return

        try:
            self.status.emit(self.peer_name, "waiting_accept")
            wire.send_json(offer)
            wire.settimeout(config.SOCKET_TIMEOUT * 6)
            resp = wire.recv_json()
            if not resp.get("accept"):
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, resp.get("reason", "rejected"))
                return
            if self.pin and resp.get("pin", "") != self.pin:
                self.status.emit(self.peer_name, "rejected")
                self.finished.emit(self.peer_name, False, "pin mismatch")
                return
            wire.settimeout(None)

            if self.kind == "text":
                wire.send_json({"type": "text_body", "text": self.text})
            else:
                self._send_files(wire)
            self.status.emit(self.peer_name, "done")
            self.finished.emit(self.peer_name, True, "")
        except TransferCancelled:
            self._emit_cancelled()
        except Exception as e:
            if self._cancel.is_set():
                self._emit_cancelled()
            else:
                self.status.emit(self.peer_name, "failed")
                self.finished.emit(self.peer_name, False, str(e))
        finally:
            self._wire = None
            try:
                wire.close()
            except Exception:
                pass

    def _emit_cancelled(self) -> None:
        self.status.emit(self.peer_name, "cancelled")
        self.finished.emit(self.peer_name, False, "cancelled")

    def _send_files(self, wire: Wire) -> None:
        self.status.emit(self.peer_name, "sending")
        total = sum(f.size for f in self.files)
        sent_before = 0
        start = time.monotonic()
        for index, spec in enumerate(self.files):
            if self._cancel.is_set():
                raise TransferCancelled()
            wire.send_json({"type": "file_begin", "index": index, "name": spec.name, "size": spec.size})
            digest = self._stream(wire, spec, sent_before, total, start)
            wire.send_json({"type": "file_end", "index": index, "sha256": digest})
            sent_before += spec.size
        wire.send_json({"type": "all_done"})

    def _stream(self, wire: Wire, spec: FileSpec, sent_before: int, total: int, start: float) -> str:
        hasher = native.sha256_streaming()
        file_done = 0
        last = 0.0  # ensures progress emits on the very first chunk
        with open(spec.path, "rb", buffering=1 << 20) as f:
            while file_done < spec.size:
                if self._cancel.is_set():
                    raise TransferCancelled()
                remaining = spec.size - file_done
                buf = f.read(min(_CHUNK, remaining))
                if not buf:
                    raise IOError(f"unexpected EOF in {spec.path}")
                hasher.update(buf)
                wire.send_data(buf)
                file_done += len(buf)
                now = time.monotonic()
                if now - last >= _PROGRESS_INTERVAL or file_done == spec.size:
                    done = min(sent_before + file_done, total)
                    bps = done / max(now - start, 1e-6)
                    self.progress.emit(self.peer_name, spec.name, done, total,
                                       bps, fmt_eta(max(total - done, 0), bps))
                    last = now
        return hasher.hexdigest()


class TransferQueue(QObject):
    """Caps concurrent OUTBOUND transfers. Inbound is unbounded by design."""

    def __init__(self, max_concurrent: int = config.MAX_CONCURRENT_TRANSFERS) -> None:
        super().__init__()
        self._sema = threading.Semaphore(max_concurrent)

    def submit(self, task: TransferTask) -> None:
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def _run(self, task: TransferTask) -> None:
        task.status.emit(task.peer_name, "queued")
        if task.cancelled:
            task._emit_cancelled()
            return
        with self._sema:
            if task.cancelled:
                task._emit_cancelled()
                return
            task.run()


def _verify_and_pin_peer(wire: Wire, expected_peer_id: str | None) -> tuple[bool, str]:
    if not expected_peer_id:
        return True, ""
    fp = wire.peer_fingerprint()
    if not fp:
        return False, "peer certificate missing"
    if fp[:len(expected_peer_id)] != expected_peer_id:
        return False, "peer identity mismatch"
    ok, reason = storage.check_and_pin(expected_peer_id, fp)
    if not ok:
        return False, reason or "pinned fingerprint mismatch"
    return True, ""


def connect_and_offer(addr: str, port: int, offer: dict,
                      accept_timeout: float = 180.0,
                      expected_peer_id: str | None = None) -> Wire:
    """Connect, verify peer identity, send offer, wait for accept."""
    w = protocol.connect(addr, port)
    try:
        ok, reason = _verify_and_pin_peer(w, expected_peer_id)
        if not ok:
            raise RuntimeError(reason)
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
