"""mDNS peer discovery via Zeroconf.

Each peer advertises itself with a stable peer_id (SHA-256 prefix of its
self-signed TLS cert). The id survives device renames and IP changes and
cannot be forged without the private key.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

from . import config, crypto_utils
from .util import local_ip

_log = logging.getLogger(__name__)
_HEARTBEAT = 20  # seconds


def _peer_id() -> str:
    cert, _ = crypto_utils.ensure_cert()
    return hashlib.sha256(Path(cert).read_bytes()).hexdigest()[:16]


@dataclass
class Peer:
    peer_id: str
    name: str
    address: str
    port: int
    status: str = "online"   # "online" | "offline"
    muted: bool = False

    @property
    def display(self) -> str:
        dot = "🟢" if self.status == "online" else "🔴"
        mute = " 🔇" if self.muted else ""
        return f"{dot} {self.name}{mute}"


class PeerRegistry(QObject):
    peer_added = pyqtSignal(object)
    peer_updated = pyqtSignal(object)
    peer_removed = pyqtSignal(str)

    def __init__(self, device_name: str, online: bool, muted: set[str] | None = None) -> None:
        super().__init__()
        self.peer_id = _peer_id()
        self.device_name = device_name
        self.online = online
        self._muted: set[str] = set(muted or ())
        self.peers: dict[str, Peer] = {}
        self._svc_to_pid: dict[str, str] = {}

        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wakeup = threading.Event()

    # ---- lifecycle ----
    def start(self) -> None:
        self._zc = Zeroconf()
        self._info = self._build_info()
        self._zc.register_service(self._info, allow_name_change=True)
        self._browser = ServiceBrowser(self._zc, config.SERVICE_TYPE, handlers=[self._on_change])
        threading.Thread(target=self._announce_loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        self._zc.close()
        self._zc = None
        self._info = None
        self._browser = None

    # ---- self advertisement ----
    def _service_name(self) -> str:
        safe = "".join(c for c in self.device_name if c.isalnum() or c in "-_ ")[:40] or "device"
        return f"{safe}-{self.peer_id}.{config.SERVICE_TYPE}"

    def _build_info(self, name: str | None = None) -> ServiceInfo:
        return ServiceInfo(
            type_=config.SERVICE_TYPE,
            name=name or self._service_name(),
            addresses=[socket.inet_aton(local_ip())],
            port=config.TCP_PORT,
            properties={
                b"name": self.device_name.encode("utf-8"),
                b"status": b"online" if self.online else b"offline",
                b"id": self.peer_id.encode("ascii"),
                b"ts": str(int(time.time())).encode("ascii"),
            },
            server=f"{socket.gethostname()}-{self.peer_id}.local.",
            host_ttl=60, other_ttl=60,
        )

    def _announce_loop(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(timeout=_HEARTBEAT)
            self._wakeup.clear()
            if self._stop.is_set():
                return
            self._republish()

    def _republish(self) -> None:
        with self._lock:
            if self._zc is None or self._info is None:
                return
            fresh = self._build_info(self._info.name)
            try:
                self._zc.update_service(fresh)
                self._info = fresh
                return
            except Exception as e:
                _log.debug("update_service failed, re-registering: %s", e)
            try:
                self._zc.unregister_service(self._info)
            except Exception:
                pass
            try:
                self._zc.register_service(fresh, allow_name_change=True)
                self._info = fresh
            except Exception as e:
                _log.debug("re-register failed: %s", e)

    def set_device_name(self, name: str) -> None:
        self.device_name = name or config.default_device_name()
        self._wakeup.set()

    def set_online(self, online: bool) -> None:
        self.online = online
        self._wakeup.set()

    # ---- mute (by fingerprint, not name) ----
    def toggle_mute(self, peer_id: str) -> bool:
        if peer_id in self._muted:
            self._muted.discard(peer_id)
        else:
            self._muted.add(peer_id)
        peer = self.peers.get(peer_id)
        if peer is not None:
            peer.muted = peer_id in self._muted
            self.peer_updated.emit(peer)
        return peer_id in self._muted

    def is_muted(self, peer_id: str) -> bool:
        return peer_id in self._muted

    @property
    def muted(self) -> set[str]:
        return set(self._muted)

    # ---- browser callback ----
    def _on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change) -> None:
        if state_change == ServiceStateChange.Removed:
            pid = self._svc_to_pid.pop(name, None)
            if pid and pid in self.peers:
                del self.peers[pid]
                self.peer_removed.emit(pid)
            return

        info = zeroconf.get_service_info(service_type, name, timeout=2000)
        if info is None:
            return
        props = info.properties or {}
        pid = (props.get(b"id") or b"").decode("ascii", "ignore")
        if not pid or pid == self.peer_id:
            return

        self._svc_to_pid[name] = pid
        status = (props.get(b"status") or b"online").decode("ascii", "ignore").strip().lower()
        peer = Peer(
            peer_id=pid,
            name=(props.get(b"name") or b"").decode("utf-8", "ignore") or name,
            address=socket.inet_ntoa(info.addresses[0]) if info.addresses else "",
            port=info.port or config.TCP_PORT,
            status="offline" if status == "offline" else "online",
            muted=pid in self._muted,
        )
        existing = self.peers.get(pid)
        self.peers[pid] = peer
        (self.peer_updated if existing else self.peer_added).emit(peer)

    # ---- lookups ----
    def find_by_name(self, name: str) -> Peer | None:
        return next((p for p in self.peers.values() if p.name == name), None)
