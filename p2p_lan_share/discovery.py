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

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
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
    _internal_change_signal = pyqtSignal(str, str, str)

    def __init__(self, device_name: str, online: bool, muted: set[str] | None = None) -> None:
        super().__init__()
        self.peer_id = _compute_peer_id()
        self.device_name = device_name
        self.online = online
        # Stores peer_ids (cert fingerprints), not names.
        self._muted: set[str] = set(muted or ())
        self.peers: dict[str, Peer] = {}  # peer_id -> Peer
        self._pending_removals: dict[str, QTimer] = {}

        self._zc: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._internal_change_signal.connect(self._process_change_on_main)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._zc = Zeroconf()
        self._register()
        self._browser = ServiceBrowser(self._zc, config.SERVICE_TYPE, handlers=[self._on_change])

    def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._browser is not None:
                self._browser.cancel()
        except Exception:
            pass
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        
        # Briefly wait to allow the mDNS "Goodbye" packet to actually transmit 
        # over the network before we blindly kill the zeroconf sockets.
        import time
        time.sleep(0.15)
        
        try:
            self._zc.close()
        except Exception:
            pass
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

    def _reannounce(self) -> None:
        """Update the mDNS record in the background to avoid UI delays."""
        if self._zc is None:
            return

        def do_update() -> None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
            except Exception:
                pass
            self._info = self._build_info()
            try:
                self._zc.register_service(self._info, allow_name_change=True)
            except Exception:
                pass

        import threading
        threading.Thread(target=do_update, daemon=True).start()

    def set_device_name(self, name: str) -> None:
        self.device_name = name or config.default_device_name()
        self._reannounce()

    def set_online(self, online: bool) -> None:
        self.online = online
        self._reannounce()

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
    def _execute_removal(self, pid: str) -> None:
        if pid in self._pending_removals:
            timer = self._pending_removals.pop(pid)
            timer.deleteLater()
        if pid in self.peers:
            del self.peers[pid]
            self.peer_removed.emit(pid)

    def _on_change(self, zeroconf: Zeroconf, service_type: str, name: str, state_change) -> None:
        # Passes the event from the background thread to the main thread securely
        self._internal_change_signal.emit(service_type, name, str(state_change))

    def _process_change_on_main(self, service_type: str, name: str, state_change_str: str) -> None:
        if "Removed" in state_change_str:
            # find by service name
            pid = self._pid_from_service(name)
            if pid and pid in self.peers:
                # Delay removal to prevent UI flicker when peers update their settings
                if pid not in self._pending_removals:
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(lambda p=pid: self._execute_removal(p))
                    timer.start(1200) # Wait 1.2s before actually removing
                    self._pending_removals[pid] = timer
            return

        # For Added/Updated state_change we query the zeroconf cache
        if self._zc is None:
            return
        info = self._zc.get_service_info(service_type, name, timeout=2000)
        if info is None:
            return
        props = info.properties or {}
        pid = (props.get(b"id") or b"").decode("ascii", "ignore")
        if not pid or pid == self.peer_id:
            return  # skip self

        # Cancel any pending removal since the peer is back
        if pid in self._pending_removals:
            timer = self._pending_removals.pop(pid)
            timer.stop()
            timer.deleteLater()

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
