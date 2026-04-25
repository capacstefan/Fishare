"""mDNS service broadcast + peer browser using Zeroconf.

Each peer advertises:
  - service name:  <device_name>.<uuid>._p2planshare._tcp.local.
  - TXT record:    name=<device_name>, status=online|offline, id=<uuid>
  - port:          TCP transfer port

PeerRegistry emits Qt signals so the GUI updates live.
"""
from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

from . import config, crypto_utils


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


def _local_ip() -> str:
    """Best-effort LAN IP address detection on Windows."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class PeerRegistry(QObject):
    """Owns the Zeroconf instance, broadcasts self, and tracks discovered peers."""

    peer_added = pyqtSignal(object)       # Peer
    peer_removed = pyqtSignal(str)        # peer_id
    peer_updated = pyqtSignal(object)     # Peer

    def __init__(self, device_name: str, online: bool) -> None:
        super().__init__()
        self.peer_id = _compute_peer_id()
        self.device_name = device_name
        self.online = online
        self._muted: set[str] = set()  # stores peer_ids (fingerprints), not names
        self.peers: dict[str, Peer] = {}  # peer_id -> Peer

        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._zc = Zeroconf()
        self._register()
        self._browser = ServiceBrowser(self._zc, config.SERVICE_TYPE, handlers=[self._on_change])

    def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        self._zc.close()
        self._zc = None

    # ---------- self advertisement ----------
    def _service_name(self) -> str:
        # Must end with service type; keep unique via peer_id
        safe = "".join(c for c in self.device_name if c.isalnum() or c in "-_ ")[:40] or "device"
        return f"{safe}-{self.peer_id}.{config.SERVICE_TYPE}"

    def _build_info(self) -> ServiceInfo:
        ip = _local_ip()
        props = {
            b"name": self.device_name.encode("utf-8"),
            b"status": (b"online" if self.online else b"offline"),
            b"id": self.peer_id.encode("ascii"),
        }
        return ServiceInfo(
            type_=config.SERVICE_TYPE,
            name=self._service_name(),
            addresses=[socket.inet_aton(ip)],
            port=config.TCP_PORT,
            properties=props,
            server=f"{socket.gethostname()}-{self.peer_id}.local.",
        )

    def _register(self) -> None:
        assert self._zc is not None
        self._info = self._build_info()
        self._zc.register_service(self._info, allow_name_change=True)

    def _update_props(self) -> None:
        """Refresh TXT record in place (no goodbye/hello cycle).

        Other peers see a property update instead of a remove+add, so a name
        change or online/offline flip is instant and doesn't cause flicker.
        """
        if self._zc is None or self._info is None:
            return
        old_info = self._info
        new_info = self._build_info()
        self._info = new_info
        try:
            if new_info.name == old_info.name:
                self._zc.update_service(new_info)
            else:
                # Name changed (device rename) - re-register cleanly.
                self._zc.unregister_service(old_info)
                self._zc.register_service(new_info, allow_name_change=True)
        except Exception:
            # Fallback: full re-register if update is unsupported for any reason.
            try:
                self._zc.unregister_service(old_info)
            except Exception:
                pass
            self._zc.register_service(new_info, allow_name_change=True)

    def set_device_name(self, name: str) -> None:
        self.device_name = name or config.default_device_name()
        self._update_props()

    def set_online(self, online: bool) -> None:
        self.online = online
        self._update_props()

    # ---------- muting (by peer_id / fingerprint) ----------
    def set_muted(self, muted: set[str]) -> None:
        self._muted = set(muted)
        for p in self.peers.values():
            new = p.peer_id in self._muted
            if new != p.muted:
                p.muted = new
                self.peer_updated.emit(p)

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
            # find by service name
            pid = self._pid_from_service(name)
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

        pname = (props.get(b"name") or b"").decode("utf-8", "ignore") or name
        status = (props.get(b"status") or b"online").decode("ascii", "ignore")
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

    def _pid_from_service(self, service_name: str) -> str | None:
        # Our service names end with "-<pid>.<service_type>"
        head = service_name.split(".")[0]
        if "-" in head:
            return head.rsplit("-", 1)[-1]
        return None

    # ---------- lookup helpers ----------
    def find_by_name(self, name: str) -> Peer | None:
        for p in self.peers.values():
            if p.name == name:
                return p
        return None
