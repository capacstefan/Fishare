"""One-way folder sync (sender -> receiver) using watchdog + framed protocol.

Sender's initial scan transmits every file. Then watchdog streams add/modify/
delete events. Receiver mirrors them. Either side can send a stop frame.

Wire ownership: SyncSender connects via protocol.open_offer (returning a Wire)
and owns it. SyncReceiver receives a Wire from TransferServer's sync_started
signal (the server has already exchanged the offer/response and hands off
the Wire). Polling for "stop" is gone — the framed protocol lets us block on
recv_json() and react when a stop frame arrives.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from . import config, native
from .protocol import FT_DATA, Wire, WireError


def _rel(root: Path, p: Path) -> str:
    return str(p.resolve().relative_to(root.resolve())).replace("\\", "/")


def _max_chunk() -> int:
    from .protocol import MAX_FRAME
    return min(config.CHUNK, MAX_FRAME)


# =============================================================================
# Sender
# =============================================================================
class _Handler(FileSystemEventHandler):
    def __init__(self, on_event: Callable[[str, str], None]) -> None:
        super().__init__(); self._on = on_event
    def on_created(self, e):
        if not e.is_directory: self._on("put", e.src_path)
    def on_modified(self, e):
        if not e.is_directory: self._on("put", e.src_path)
    def on_deleted(self, e):
        if not e.is_directory: self._on("delete", e.src_path)
    def on_moved(self, e):
        if e.is_directory: return
        self._on("delete", e.src_path); self._on("put", e.dest_path)


class SyncSender(QObject):
    event = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, wire: Wire, folder: str) -> None:
        super().__init__()
        self._wire = wire
        self._folder = Path(folder)
        self._observer: Optional[BaseObserver] = None
        self._stop = threading.Event()
        # Wire.send is mutex-protected internally, but a "put" needs an
        # atomic header+body sequence; this lock guards that compound op.
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self, notify: bool = True) -> None:
        if self._stop.is_set(): return
        self._stop.set()
        if notify:
            try:
                with self._lock:
                    self._wire.send_json({"type": "sync_event", "op": "stop"})
            except Exception:
                pass
        if self._observer:
            try: self._observer.stop(); self._observer.join(timeout=2)
            except Exception: pass
        self._wire.close()

    # ---- main loop ----
    def _run(self) -> None:
        try:
            for p in self._folder.rglob("*"):
                if self._stop.is_set(): return
                if p.is_file():
                    self._send_put(p)

            self._observer = Observer()
            self._observer.schedule(_Handler(self._queue_event), str(self._folder), recursive=True)
            self._observer.start()
            self.event.emit("syncing")

            # Block on the wire — the framed protocol gives us a clean stop signal.
            while not self._stop.is_set():
                msg = self._wire.recv_json()
                if msg.get("op") == "stop":
                    self.finished.emit("peer stopped")
                    self.stop(notify=False)
                    return
        except Exception as e:
            self.finished.emit(f"error: {e}")
        finally:
            self.stop(notify=False)
            self.finished.emit("stopped")

    # ---- event/data ----
    def _queue_event(self, op: str, src: str) -> None:
        try:
            p = Path(src)
            if op == "put":
                time.sleep(0.05)  # debounce mid-write
                if p.exists() and p.is_file():
                    self._send_put(p)
            elif op == "delete":
                try: rel = _rel(self._folder, p)
                except Exception: return
                with self._lock:
                    self._wire.send_json({"type": "sync_event", "op": "delete", "path": rel})
        except Exception as e:
            self.event.emit(f"sync error: {e}")

    def _send_put(self, p: Path) -> None:
        try:
            rel = _rel(self._folder, p)
            size = p.stat().st_size
        except Exception:
            return
        if size > config.MAX_FILE_SIZE:
            self.event.emit(f"skip {rel}: too large")
            return
        cap = _max_chunk()
        hasher = native.sha256_streaming()
        with self._lock:
            self._wire.send_json({"type": "sync_event", "op": "put", "path": rel, "size": size})
            with p.open("rb", buffering=1 << 20) as f:
                remaining = size
                while remaining > 0:
                    buf = f.read(min(cap, remaining))
                    if not buf:
                        break
                    hasher.update(buf)
                    self._wire.send_data(buf)
                    remaining -= len(buf)
            self._wire.send_json({"type": "sync_event", "op": "put_end", "sha256": hasher.hexdigest()})


# =============================================================================
# Receiver
# =============================================================================
class SyncReceiver(QObject):
    event = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, wire: Wire, dest_folder: str) -> None:
        super().__init__()
        self._wire = wire
        self._dest = Path(dest_folder)
        self._dest.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self, notify: bool = True) -> None:
        if self._stop.is_set(): return
        self._stop.set()
        if notify:
            try: self._wire.send_json({"type": "sync_event", "op": "stop"})
            except Exception: pass
        self._wire.close()

    def _safe_target(self, rel: str) -> Path | None:
        rel = rel.replace("\\", "/").lstrip("/")
        target = (self._dest / rel).resolve()
        try: target.relative_to(self._dest.resolve())
        except ValueError: return None
        return target

    def _run(self) -> None:
        self.event.emit("syncing")
        try:
            while not self._stop.is_set():
                msg = self._wire.recv_json()
                if msg.get("type") != "sync_event":
                    continue
                op = msg.get("op")
                if op == "stop":
                    self.finished.emit("peer stopped"); return
                if op == "put":
                    self._recv_put(msg)
                elif op == "delete":
                    target = self._safe_target(msg.get("path", ""))
                    if target and target.exists():
                        try: target.unlink()
                        except OSError: pass
        except Exception as e:
            self.finished.emit(f"error: {e}")
        finally:
            self.stop(notify=False)
            self.finished.emit("stopped")

    def _recv_put(self, msg: dict) -> None:
        size = int(msg.get("size", 0))
        if size > config.MAX_FILE_SIZE:
            raise ValueError("file too large")
        target = self._safe_target(msg.get("path", ""))
        hasher = native.sha256_streaming()
        # Receive all DATA frames totalling `size`, then a put_end JSON frame.
        # If target is rejected, drain the bytes anyway to keep the stream aligned.
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            sink = target.open("wb", buffering=1 << 20)
        else:
            sink = None
        try:
            received = 0
            while received < size:
                ftype, payload = self._wire.recv_frame()
                if ftype != FT_DATA:
                    raise WireError(f"expected data frame, got {ftype!r}")
                if not payload:
                    raise ConnectionError("closed mid-file")
                if received + len(payload) > size:
                    raise WireError("data overflow")
                if sink: sink.write(payload)
                hasher.update(payload)
                received += len(payload)
            tail = self._wire.recv_json()
            if tail.get("type") != "sync_event" or tail.get("op") != "put_end":
                raise WireError("missing put_end")
            if tail.get("sha256") != hasher.hexdigest():
                raise WireError("integrity check failed")
        finally:
            if sink: sink.close()
