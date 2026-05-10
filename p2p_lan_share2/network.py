"""TLS networking: transfer server, client, queue."""
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

from . import config, native
from .protocol import (
    FT_DATA, MAX_FRAME, Wire, WireError,
    open_offer, server_ctx, tune_sock,
)
from .util import unique_path


# Re-export for GUI convenience.
connect_and_offer = open_offer


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


def _max_data_chunk() -> int:
    return min(config.CHUNK, MAX_FRAME)


@dataclass
class FileSpec:
    path: str
    size: int

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class IncomingOffer:
    """Pending incoming transfer awaiting user decision."""

    kind: str
    sender_name: str
    sender_id: str
    files: list = field(default_factory=list)
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


class TransferServer(QObject):
    offer_received = pyqtSignal(object)
    file_progress = pyqtSignal(str, str, int, int, float, str)
    transfer_completed = pyqtSignal(str, list, int)
    recv_failed = pyqtSignal(str, str)
    text_received = pyqtSignal(str, str)
    sync_started = pyqtSignal(str, str, object)  # name, folder, Wire
    log = pyqtSignal(str)

    def __init__(self, get_state: Callable[[], dict]) -> None:
        super().__init__()
        self._get_state = get_state
        self._sock: socket.socket | None = None
        self._ctx: ssl.SSLContext | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._ctx = server_ctx()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", config.TCP_PORT))
        self._sock.listen(16)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw, addr = self._sock.accept()
            except OSError:
                return
            tune_sock(raw)
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
            offer_msg = wire.recv_json()
            if offer_msg.get("type") != "offer":
                wire.send_json({"type": "response", "accept": False, "reason": "bad protocol"})
                return

            state = self._get_state()
            sender_name = offer_msg.get("from", "Unknown")
            sender_id = offer_msg.get("from_id", "")
            kind = offer_msg.get("kind", "")

            if not state["online"]:
                wire.send_json({"type": "response", "accept": False, "reason": "offline"})
                return
            if sender_id in state["muted"]:
                wire.send_json({"type": "response", "accept": False, "reason": "muted"})
                return

            files = offer_msg.get("files", []) or []
            total_size = int(offer_msg.get("total_size", 0))

            if kind == "files" and (
                total_size > config.MAX_FILE_SIZE
                or any(int(f.get("size", 0)) > config.MAX_FILE_SIZE for f in files)
            ):
                wire.send_json({"type": "response", "accept": False, "reason": "too large"})
                return

            offer = IncomingOffer(
                kind=kind,
                sender_name=sender_name,
                sender_id=sender_id,
                files=files,
                total_size=total_size,
                pin_required=bool(offer_msg.get("pin_required", False)),
                folder=offer_msg.get("folder", ""),
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
                self.text_received.emit(sender_name, str(body.get("text", "")))
            elif kind == "files":
                try:
                    self._recv_files(wire, offer, state["download_dir"])
                except Exception as e:
                    self.recv_failed.emit(sender_name, str(e))
                    raise
            elif kind == "sync":
                self.sync_started.emit(sender_name, offer.folder, wire)
                keep_open = True
        except Exception as e:
            self.log.emit(f"Receiver error from {addr}: {e}")
        finally:
            if not keep_open:
                wire.close()

    def _recv_files(self, wire: Wire, offer: IncomingOffer, download_dir: str) -> None:
        dest = Path(download_dir)
        dest.mkdir(parents=True, exist_ok=True)
        total = sum(int(f.get("size", 0)) for f in offer.files)
        done = 0
        start = time.monotonic()
        last_emit = start
        received_names: list[str] = []

        for _ in offer.files:
            hdr = wire.recv_json()
            if hdr.get("type") != "file_begin":
                raise WireError(f"unexpected frame: {hdr.get('type')}")
            name = os.path.basename(hdr.get("name", "file"))
            size = int(hdr.get("size", 0))
            part = dest / (name + ".part")
            try:
                part.unlink()
            except FileNotFoundError:
                pass

            hasher = native.sha256_streaming()
            file_done = 0
            with part.open("wb", buffering=1 << 20) as f:
                while file_done < size:
                    ftype, payload = wire.recv_frame()
                    if ftype != FT_DATA:
                        raise WireError(f"expected data, got {ftype!r}")
                    if not payload:
                        raise ConnectionError("unexpected empty data frame")
                    if file_done + len(payload) > size:
                        raise WireError("data overflow")
                    f.write(payload)
                    hasher.update(payload)
                    file_done += len(payload)
                    done += len(payload)
                    now = time.monotonic()
                    if now - last_emit >= 0.1 or done == total:
                        bps = done / max(now - start, 1e-6)
                        eta = _human_eta(total - done, bps)
                        self.file_progress.emit(offer.sender_name, name, done, total, bps, eta)
                        last_emit = now

            tail = wire.recv_json()
            if tail.get("type") != "file_end" or tail.get("sha256") != hasher.hexdigest():
                try:
                    part.unlink()
                except OSError:
                    pass
                raise WireError("integrity check failed")

            final = unique_path(dest / name)
            try:
                part.replace(final)
            except OSError as e:
                raise WireError(f"finalize failed: {e}")
            received_names.append(final.name)

        end = wire.recv_json()
        if end.get("type") != "all_done":
            raise WireError("missing all_done")
        self.transfer_completed.emit(offer.sender_name, received_names, done)


class TransferTask(QObject):
    progress = pyqtSignal(str, str, int, int, float, str)
    status = pyqtSignal(str, str)
    finished = pyqtSignal(str, bool, str)

    def __init__(self, peer_name, peer_addr, peer_port, kind,
                 from_name, from_id, files=None, text="", pin="") -> None:
        super().__init__()
        self.peer_name = peer_name
        self.peer_addr = peer_addr
        self.peer_port = peer_port
        self.kind = kind
        self.from_name = from_name
        self.from_id = from_id
        self.files = files or []
        self.text = text
        self.pin = pin

    def run(self) -> None:
        offer = self._build_offer()
        if offer is None:
            self.finished.emit(self.peer_name, False, "unknown kind")
            return
        try:
            from .protocol import connect_wire
            wire = connect_wire(self.peer_addr, self.peer_port)
        except Exception as e:
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, f"connect: {e}")
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
        except Exception as e:
            self.status.emit(self.peer_name, "failed")
            self.finished.emit(self.peer_name, False, str(e))
        finally:
            wire.close()

    def _build_offer(self) -> dict | None:
        if self.kind == "files":
            return {
                "type": "offer",
                "kind": "files",
                "from": self.from_name,
                "from_id": self.from_id,
                "pin_required": bool(self.pin),
                "files": [{"name": f.name, "size": f.size} for f in self.files],
                "total_size": sum(f.size for f in self.files),
            }
        if self.kind == "text":
            return {
                "type": "offer",
                "kind": "text",
                "from": self.from_name,
                "from_id": self.from_id,
            }
        return None

    def _send_files(self, wire: Wire) -> None:
        self.status.emit(self.peer_name, "sending")
        total = sum(f.size for f in self.files)
        sent_before = 0
        start = time.monotonic()
        chunk_cap = _max_data_chunk()
        for index, spec in enumerate(self.files):
            wire.send_json({
                "type": "file_begin",
                "index": index,
                "name": spec.name,
                "size": spec.size,
            })
            sha256_hex = self._stream_one(wire, spec, sent_before, total, start, chunk_cap)
            wire.send_json({"type": "file_end", "index": index, "sha256": sha256_hex})
            sent_before += spec.size
        wire.send_json({"type": "all_done"})

    def _stream_one(self, wire: Wire, spec: FileSpec,
                    sent_before: int, total: int, start: float, chunk_cap: int) -> str:
        hasher = native.sha256_streaming()
        file_done = 0
        last_emit = start
        with open(spec.path, "rb", buffering=1 << 20) as f:
            while file_done < spec.size:
                buf = f.read(chunk_cap)
                if not buf:
                    raise IOError(f"unexpected EOF in {spec.path}")
                hasher.update(buf)
                wire.send_data(buf)
                file_done += len(buf)
                now = time.monotonic()
                if now - last_emit >= 0.1 or file_done == spec.size:
                    done = sent_before + file_done
                    bps = done / max(now - start, 1e-6)
                    eta = _human_eta(total - done, bps)
                    self.progress.emit(self.peer_name, spec.name, done, total, bps, eta)
                    last_emit = now
        return hasher.hexdigest()


class TransferQueue(QObject):
    """Caps concurrent outbound transfers."""

    def __init__(self, max_concurrent: int = config.MAX_CONCURRENT_TRANSFERS) -> None:
        super().__init__()
        self._sema = threading.Semaphore(max_concurrent)

    def submit(self, task: TransferTask) -> None:
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def _run(self, task: TransferTask) -> None:
        task.status.emit(task.peer_name, "queued")
        with self._sema:
            task.run()
