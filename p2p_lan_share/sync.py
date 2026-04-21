"""One-way folder sync (sender -> receiver) using watchdog.

On start, sender performs initial scan and transmits every file. Then watchdog
streams events. Receiver mirrors add/modify/delete. Either side sends "stop".
Messages reuse the same TLS socket created by network.py after accept.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config


def _send_json(sock: ssl.SSLSocket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _recv_line(sock: ssl.SSLSocket) -> str:
    buf = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("closed")
        if ch == b"\n":
            break
        buf += ch
    return buf.decode("utf-8")


def _recv_json(sock: ssl.SSLSocket) -> dict:
    return json.loads(_recv_line(sock))


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(min(config.CHUNK_LARGE, n - len(out)))
        if not chunk:
            raise ConnectionError("closed")
        out += chunk
    return bytes(out)


def _rel(root: Path, p: Path) -> str:
    return str(p.resolve().relative_to(root.resolve())).replace("\\", "/")


# =============================================================================
# Sender side
# =============================================================================
class _SyncEventHandler(FileSystemEventHandler):
    def __init__(self, on_event: Callable[[str, str, str], None]) -> None:
        super().__init__()
        self._on = on_event  # (op, src_path, dest_path_or_empty)

    def on_created(self, event):
        if not event.is_directory:
            self._on("put", event.src_path, "")

    def on_modified(self, event):
        if not event.is_directory:
            self._on("put", event.src_path, "")

    def on_deleted(self, event):
        if not event.is_directory:
            self._on("delete", event.src_path, "")

    def on_moved(self, event):
        if event.is_directory:
            return
        self._on("delete", event.src_path, "")
        self._on("put", event.dest_path, "")


class SyncSender(QObject):
    event = pyqtSignal(str)
    finished = pyqtSignal(str)  # reason

    def __init__(self, sock: ssl.SSLSocket, folder: str) -> None:
        super().__init__()
        self._sock = sock
        self._folder = Path(folder)
        self._observer: Observer | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, notify: bool = True) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if notify:
            try:
                with self._lock:
                    _send_json(self._sock, {"type": "sync_event", "op": "stop"})
            except Exception:
                pass
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
        try:
            self._sock.close()
        except Exception:
            pass

    def _run(self) -> None:
        try:
            # 1) Initial scan: send every current file
            for p in self._folder.rglob("*"):
                if p.is_file():
                    self._send_put(p)
                if self._stop.is_set():
                    return

            # 2) Start watchdog
            handler = _SyncEventHandler(self._queue_event)
            self._observer = Observer()
            self._observer.schedule(handler, str(self._folder), recursive=True)
            self._observer.start()
            self.event.emit("syncing")

            # 3) Watch for "stop" from receiver
            self._sock.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    msg = _recv_json(self._sock)
                    if msg.get("op") == "stop":
                        self.finished.emit("peer stopped")
                        self.stop(notify=False)
                        return
                except (TimeoutError, ssl.SSLWantReadError):
                    continue
                except Exception:
                    break
        except Exception as e:
            self.finished.emit(f"error: {e}")
        finally:
            self.stop(notify=False)
            self.finished.emit("stopped")

    def _queue_event(self, op: str, src: str, _dest: str) -> None:
        try:
            p = Path(src)
            if op == "put":
                # small debounce: file may still be being written
                time.sleep(0.05)
                if p.exists() and p.is_file():
                    self._send_put(p)
            elif op == "delete":
                try:
                    rel = _rel(self._folder, p)
                except Exception:
                    return
                with self._lock:
                    _send_json(self._sock, {"type": "sync_event", "op": "delete", "path": rel})
        except Exception as e:
            self.event.emit(f"sync error: {e}")

    def _send_put(self, p: Path) -> None:
        try:
            rel = _rel(self._folder, p)
            size = p.stat().st_size
        except Exception:
            return
        with self._lock:
            _send_json(self._sock, {"type": "sync_event", "op": "put", "path": rel, "size": size})
            with p.open("rb") as f:
                remaining = size
                while remaining > 0:
                    chunk = f.read(min(config.CHUNK_LARGE, remaining))
                    if not chunk:
                        break
                    self._sock.sendall(chunk)
                    remaining -= len(chunk)


# =============================================================================
# Receiver side
# =============================================================================
class SyncReceiver(QObject):
    event = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, sock: ssl.SSLSocket, dest_folder: str) -> None:
        super().__init__()
        self._sock = sock
        self._dest = Path(dest_folder)
        self._dest.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, notify: bool = True) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if notify:
            try:
                _send_json(self._sock, {"type": "sync_event", "op": "stop"})
            except Exception:
                pass
        try:
            self._sock.close()
        except Exception:
            pass

    def _safe_target(self, rel: str) -> Path | None:
        # Prevent path traversal
        rel = rel.replace("\\", "/").lstrip("/")
        target = (self._dest / rel).resolve()
        try:
            target.relative_to(self._dest.resolve())
        except ValueError:
            return None
        return target

    def _run(self) -> None:
        self.event.emit("syncing")
        try:
            self._sock.settimeout(None)
            while not self._stop.is_set():
                msg = _recv_json(self._sock)
                if msg.get("type") != "sync_event":
                    continue
                op = msg.get("op")
                if op == "stop":
                    self.finished.emit("peer stopped")
                    return
                if op == "put":
                    target = self._safe_target(msg.get("path", ""))
                    size = int(msg.get("size", 0))
                    if target is None:
                        # discard the bytes to keep stream aligned
                        _recv_exact(self._sock, size)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    remaining = size
                    with target.open("wb") as f:
                        while remaining > 0:
                            chunk = self._sock.recv(min(config.CHUNK_LARGE, remaining))
                            if not chunk:
                                raise ConnectionError("closed mid-file")
                            f.write(chunk)
                            remaining -= len(chunk)
                elif op == "delete":
                    target = self._safe_target(msg.get("path", ""))
                    if target and target.exists():
                        try:
                            target.unlink()
                        except Exception:
                            pass
        except Exception as e:
            self.finished.emit(f"error: {e}")
        finally:
            self.stop(notify=False)
            self.finished.emit("stopped")
