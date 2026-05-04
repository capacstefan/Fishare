"""mDNS service broadcast + peer browser using Zeroconf.

Each peer advertises:
  - service name:  <device_name>.<uuid>._p2planshare._tcp.local.
  - TXT record:    name=<device_name>, status=online|offline, id=<uuid>
  - port:          TCP transfer port

PeerRegistry emits Qt signals so the GUI updates live.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

from . import config, crypto_utils
from .util import local_ip


_log = logging.getLogger(__name__)

# Periodic re-announce so peers refresh TXT records even if an update event is
# missed on the network/OS stack.
_ANNOUNCE_HEARTBEAT_SEC = 20


def _compute_peer_id() -> str:
    """Stable identity = SHA-256 prefix of our self-signed TLS cert.

    The cert is generated once by crypto_utils and persists in %APPDATA%,
    so this id survives device renames and IP changes. An attacker cannot
    forge it without access to our private key.
    """
    cert_path, _ = crypto_utils.ensure_cert()
    der = Path(cert_path).read_bytes()
    return hashlib.sha256(der).hexdigest()[:16]


@dataclass
class Peer:
    peer_id: str
    name: str
    address: str
    port: int
    status: str = "online"  # "online" | "offline"
    muted: bool = False

    @property
    def display(self) -> str:
        dot = "🟢" if self.status == "online" else "🔴"
        mute = " 🔇" if self.muted else ""
        return f"{dot} {self.name}{mute}"


# Backwards-compat alias (web_server.py imports this name historically).
_local_ip = local_ip


class PeerRegistry(QObject):
    """Owns the Zeroconf instance, broadcasts self, and tracks discovered peers."""

    peer_added = pyqtSignal(object)       # Peer
    peer_removed = pyqtSignal(str)        # peer_id
    peer_updated = pyqtSignal(object)     # Peer

    def __init__(self, device_name: str, online: bool, muted: set[str] | None = None) -> None:
        super().__init__()
        self.peer_id = _compute_peer_id()
        self.device_name = device_name
        self.online = online
        # Stores peer_ids (cert fingerprints), not names.
        self._muted: set[str] = set(muted or ())
        self.peers: dict[str, Peer] = {}  # peer_id -> Peer
        self._svc_to_pid: dict[str, str] = {}  # zeroconf service name -> peer_id

        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._announce_lock = threading.Lock()

        # Serialize all announce operations through one thread to avoid
        # overlapping update/unregister/register calls.
        self._announce_stop = threading.Event()
        self._announce_wakeup = threading.Event()
        self._announce_thread: threading.Thread | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._zc = Zeroconf()
        self._register()
        self._browser = ServiceBrowser(self._zc, config.SERVICE_TYPE, handlers=[self._on_change])

        self._announce_stop.clear()
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
        self._announce_thread.start()

    def stop(self) -> None:
        if self._zc is None:
            return
        self._announce_stop.set()
        self._announce_wakeup.set()
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        self._zc.close()
        self._zc = None
        self._info = None
        self._browser = None

    # ---------- self advertisement ----------
    def _service_name(self) -> str:
        # Must end with service type; keep unique via peer_id
        safe = "".join(c for c in self.device_name if c.isalnum() or c in "-_ ")[:40] or "device"
        return f"{safe}-{self.peer_id}.{config.SERVICE_TYPE}"

    def _current_properties(self) -> dict:
        return {
            b"name": self.device_name.encode("utf-8"),
            b"status": (b"online" if self.online else b"offline"),
            b"id": self.peer_id.encode("ascii"),
            # Monotonic refresh marker (helps peers detect changes even when
            # values briefly repeat; also enables future 'last seen' logic).
            b"ts": str(int(time.time())).encode("ascii"),
        }

    def _build_info(self, service_name: str | None = None) -> ServiceInfo:
        ip = local_ip()
        return ServiceInfo(
            type_=config.SERVICE_TYPE,
            name=service_name or self._service_name(),
            addresses=[socket.inet_aton(ip)],
            port=config.TCP_PORT,
            properties=self._current_properties(),
            server=f"{socket.gethostname()}-{self.peer_id}.local.",
            # Short TTL so peers drop us within ~a minute if our process
            # is killed without a clean goodbye.
            host_ttl=60,
            other_ttl=60,
        )

    def _register(self) -> None:
        assert self._zc is not None
        self._info = self._build_info()
        self._zc.register_service(self._info, allow_name_change=True)

    def _request_announce(self) -> None:
        """Wake the announce loop to publish updated TXT properties."""
        self._announce_wakeup.set()

    def _announce_loop(self) -> None:
        """Publish TXT updates reliably.

        We periodically refresh our service info (heartbeat) and also wake up
        immediately when the user toggles Online/Offline or edits device name.
        """
        while not self._announce_stop.is_set():
            # Wake on explicit request or heartbeat.
            self._announce_wakeup.wait(timeout=_ANNOUNCE_HEARTBEAT_SEC)
            self._announce_wakeup.clear()
            if self._announce_stop.is_set():
                return
            try:
                self._announce_once()
            except Exception as e:
                _log.debug("announce failed: %s", e)

    def _announce_once(self) -> None:
        if self._zc is None or self._info is None:
            return
        with self._announce_lock:
            if self._zc is None or self._info is None:
                return

            zc = self._zc
            info = self._info
            fresh = self._build_info(service_name=info.name)
            try:
                zc.update_service(fresh)
                self._info = fresh
                return
            except Exception as e:
                # Some platforms/stacks are flaky about TXT-only updates.
                # Fall back to a full unregister+register with the same name.
                _log.debug("update_service failed; falling back to re-register: %s", e)
            try:
                zc.unregister_service(info)
            except Exception:
                pass
            try:
                zc.register_service(fresh, allow_name_change=True)
                self._info = fresh
            except Exception as e:
                _log.debug("re-register failed: %s", e)

    def set_device_name(self, name: str) -> None:
        self.device_name = name or config.default_device_name()
        self._request_announce()

    def set_online(self, online: bool) -> None:
        self.online = online
        self._request_announce()

    # ---------- muting (by peer_id / fingerprint) ----------
    def toggle_mute(self, peer_id: str) -> bool:
        """Mute/unmute by stable peer_id. Returns new muted state."""
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

    # ---------- browser callback ----------
    def _on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change) -> None:
        from zeroconf import ServiceStateChange
        if state_change == ServiceStateChange.Removed:
            # Robust: service names may be auto-renamed; use mapping learned
            # from TXT records instead of parsing the name.
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
            return  # skip self

        # Remember which service name maps to this peer_id so we can handle
        # ServiceStateChange.Removed reliably.
        self._svc_to_pid[name] = pid

        pname = (props.get(b"name") or b"").decode("utf-8", "ignore") or name
        status_raw = (props.get(b"status") or b"online").decode("ascii", "ignore").strip().lower()
        status = "offline" if status_raw == "offline" else "online"
        addr = socket.inet_ntoa(info.addresses[0]) if info.addresses else ""
        port = info.port or config.TCP_PORT

        existing = self.peers.get(pid)
        peer = Peer(
            peer_id=pid,
            name=pname,
            address=addr,
            port=port,
            status=status,
            muted=pid in self._muted,
        )
        self.peers[pid] = peer
        if existing is None:
            self.peer_added.emit(peer)
        else:
            self.peer_updated.emit(peer)

    # ---------- lookup helpers ----------
    def find_by_name(self, name: str) -> Peer | None:
        for p in self.peers.values():
            if p.name == name:
                return p
        return None
