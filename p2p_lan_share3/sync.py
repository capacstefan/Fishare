"""One-way folder sync over an established Wire (watchdog-driven)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config, native
from .protocol import FT_DATA, MAX_FRAME, Wire, WireError

_CHUNK = min(config.CHUNK, MAX_FRAME)


def _rel(root: Path, p: Path) -> str:
    return str(p.resolve().relative_to(root.resolve())).replace("\\", "/")


class _Handler(FileSystemEventHandler):
    def __init__(self, cb: Callable[[str, str], None]) -> None:
        super().__init__()
        self._cb = cb

    def on_created(self, e):
        if not e.is_directory: self._cb("put", e.src_path)
    def on_modified(self, e):
        if not e.is_directory: self._cb("put", e.src_path)
    def on_deleted(self, e):
        if not e.is_directory: self._cb("delete", e.src_path)
    def on_moved(self, e):
        if e.is_directory: return
        self._cb("delete", e.src_path); self._cb("put", e.dest_path)


# =============================================================================
# Sender
# =============================================================================
class SyncSender(QObject):
    event = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, wire: Wire, folder: str) -> None:
        super().__init__()
        self._wire = wire
        self._folder = Path(folder)
        self._observer: Observer | None = None
        self._stop = threading.Event()
        # Wire.send is mutex-protected, but a "put" needs an atomic
        # header+body sequence; this lock guards that compound op.
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self, notify: bool = True) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if notify:
            try:
                with self._lock:
                    self._wire.send_json({"type": "sync_event", "op": "stop"})
            except Exception:
                pass
        if self._observer:
            try:
                self._observer.stop(); self._observer.join(timeout=2)
            except Exception:
                pass
        self._wire.close()

    def _run(self) -> None:
        reason = "stopped"
        try:
            for p in self._folder.rglob("*"):
                if self._stop.is_set(): return
                if p.is_file():
                    self._send_put(p)

            self._observer = Observer()
            self._observer.schedule(_Handler(self._on_fs), str(self._folder), recursive=True)
            self._observer.start()
            self.event.emit("syncing")

            while not self._stop.is_set():
                msg = self._wire.recv_json()
                if msg.get("op") == "stop":
                    reason = "peer stopped"
                    return
        except Exception as e:
            reason = f"error: {e}"
        finally:
            self.stop(notify=False)
            self.finished.emit(reason)

    def _on_fs(self, op: str, src: str) -> None:
        try:
            p = Path(src)
            if op == "put":
                time.sleep(0.05)  # debounce mid-write
                if p.exists() and p.is_file():
                    self._send_put(p)
            elif op == "delete":
                try:
                    rel = _rel(self._folder, p)
                except Exception:
                    return
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
        hasher = native.sha256_streaming()
        with self._lock:
            self._wire.send_json({"type": "sync_event", "op": "put", "path": rel, "size": size})
            with p.open("rb", buffering=1 << 20) as f:
                remaining = size
                while remaining > 0:
                    buf = f.read(min(_CHUNK, remaining))
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
        if self._stop.is_set():
            return
        self._stop.set()
        if notify:
            try:
                self._wire.send_json({"type": "sync_event", "op": "stop"})
            except Exception:
                pass
        self._wire.close()

    def _safe(self, rel: str) -> Path | None:
        rel = rel.replace("\\", "/").lstrip("/")
        target = (self._dest / rel).resolve()
        try:
            target.relative_to(self._dest.resolve())
        except ValueError:
            return None
        return target

    def _run(self) -> None:
        self.event.emit("syncing")
        reason = "stopped"
        try:
            while not self._stop.is_set():
                msg = self._wire.recv_json()
                if msg.get("type") != "sync_event":
                    continue
                op = msg.get("op")
                if op == "stop":
                    reason = "peer stopped"
                    return
                if op == "put":
                    self._recv_put(msg)
                elif op == "delete":
                    target = self._safe(msg.get("path", ""))
                    if target and target.exists():
                        try: target.unlink()
                        except OSError: pass
        except Exception as e:
            reason = f"error: {e}"
        finally:
            self.stop(notify=False)
            self.finished.emit(reason)

    def _recv_put(self, msg: dict) -> None:
        size = int(msg.get("size", 0))
        if size > config.MAX_FILE_SIZE:
            raise ValueError("file too large")
        target = self._safe(msg.get("path", ""))
        hasher = native.sha256_streaming()
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            sink = target.open("wb", buffering=1 << 20)
        else:
            sink = None
        try:
            received = 0
            while received < size:
                ftype, payload = self._wire.recv_frame()
                if ftype != FT_DATA or not payload:
                    raise WireError("expected data frame")
                if received + len(payload) > size:
                    raise WireError("data overflow")
                if sink:
                    sink.write(payload)
                hasher.update(payload)
                received += len(payload)
            tail = self._wire.recv_json()
            if tail.get("type") != "sync_event" or tail.get("op") != "put_end":
                raise WireError("missing put_end")
            if tail.get("sha256") != hasher.hexdigest():
                raise WireError("integrity check failed")
        finally:
            if sink:
                sink.close()
