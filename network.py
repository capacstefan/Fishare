"""Network discovery layer: multicast peer discovery for local file sharing.

Handles:
- UDP multicast advertising (device announces itself)
- Peer scanning (listens for other devices)
- Device lifecycle management (TTL and garbage collection)
- Transfer request events (Qt event for user acceptance dialogs)
"""

import json
import logging
import socket
import struct
import threading
from typing import Dict

from PyQt6.QtCore import QEvent

from protocols import ProtocolCapabilities
from state import AppStatus, Device

LOG = logging.getLogger(__name__)

MCAST_GRP = "239.255.42.99"


# ══════════════════════════════════════════════════════
#  Helper Functions
# ══════════════════════════════════════════════════════


def _get_local_ip() -> str:
    """Best-effort local IPv4 address discovery."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _make_mcast_recv(port: int) -> socket.socket:
    """Create multicast receive socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.settimeout(2.0)
    return s


def _make_mcast_send() -> socket.socket:
    """Create multicast send socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
    return s


# ══════════════════════════════════════════════════════
#  Advertiser: Broadcast presence to network
# ══════════════════════════════════════════════════════


class Advertiser:
    """Periodically broadcast device presence and capabilities via multicast.
    
    Announces:
    - Device name and network address
    - Status (available/busy)
    - Supported protocols (TCP, QUIC, etc.)
    """
    
    INTERVAL_SECONDS = 1.5
    
    def __init__(self, state, protocol_selector):
        self.state = state
        self.protocol_selector = protocol_selector
        self._local_ip = _get_local_ip()   # cached once — changes are rare
        self._stop = threading.Event()
        self._sock = _make_mcast_send()
    
    def start(self):
        """Start advertising thread."""
        threading.Thread(
            target=self._run,
            daemon=True,
            name="advertiser"
        ).start()
    
    def stop(self):
        """Stop advertising."""
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass
    
    def _run(self):
        """Main advertising loop."""
        cfg = self.state.cfg
        while not self._stop.is_set():
            try:
                # Build capabilities list
                capabilities = [
                    cap.to_dict()
                    for cap in self.protocol_selector.get_capabilities()
                ]
                
                # Create advertisement payload
                payload = json.dumps({
                    "type": "fishare_adv",
                    "name": cfg.device_name,
                    "host": self._local_ip,
                    "port": cfg.listen_port,
                    "status": self.state.status.value,
                    "protocols": capabilities,
                }).encode("utf-8")
                
                self._sock.sendto(payload, (MCAST_GRP, cfg.discovery_port))
                
            except Exception as e:
                if not self._stop.is_set():
                    LOG.warning(f"Advertise error: {e}")
            
            self._stop.wait(self.INTERVAL_SECONDS)


# ══════════════════════════════════════════════════════
#  Scanner: Discover peers on network
# ══════════════════════════════════════════════════════


class Scanner:
    """Listen for peer advertisements and maintain device list.
    
    Features:
    - Discovers peers via multicast
    - Updates device status in real-time
    - Garbage collection of stale devices (TTL enforcement)
    - Filters out self-advertisements
    """
    
    DEVICE_TTL = 6.0        # Seconds before device considered offline
    GC_INTERVAL = 2.0       # Garbage collection interval
    STALE_TRANSFER_TIMEOUT = 300.0  # 5 minutes
    
    def __init__(self, state):
        self.state = state
        self._local_ip = _get_local_ip()   # cached once
        self._stop = threading.Event()
        self._sock = _make_mcast_recv(state.cfg.discovery_port)
    
    def start(self):
        """Start scanner threads."""
        threading.Thread(
            target=self._listen,
            daemon=True,
            name="scanner"
        ).start()
        threading.Thread(
            target=self._gc,
            daemon=True,
            name="scanner-gc"
        ).start()
    
    def stop(self):
        """Stop scanner."""
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass
    
    def _gc(self):
        """Periodic garbage collection of stale devices and transfers."""
        while not self._stop.is_set():
            self.state.prune_devices(ttl_seconds=self.DEVICE_TTL)
            self.state.cleanup_stale_transfers(timeout_seconds=self.STALE_TRANSFER_TIMEOUT)
            self.state.process_pending_clears()
            self._stop.wait(self.GC_INTERVAL)
    
    def _listen(self):
        """Listen for multicast advertisements."""
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            
            try:
                payload = json.loads(data.decode("utf-8"))
                if payload.get("type") != "fishare_adv":
                    continue
                
                # Filter out self-advertisements
                adv_host = payload.get("host") or addr[0]
                adv_port = int(payload.get("port", 0))
                if not (1 <= adv_port <= 65535):
                    continue

                if adv_host == self._local_ip and adv_port == self.state.cfg.listen_port:
                    continue
                
                # Parse status
                raw_status = payload.get("status", "busy")
                status = (
                    AppStatus(raw_status)
                    if raw_status in {s.value for s in AppStatus}
                    else AppStatus.BUSY
                )
                
                # Parse protocol capabilities
                protocol_data = payload.get("protocols", [])
                protocols = []
                for p in protocol_data:
                    try:
                        cap = ProtocolCapabilities.from_dict(p)
                        if cap:
                            protocols.append(cap)
                    except Exception as e:
                        LOG.debug(f"Failed to parse protocol capability: {e}")
                
                # Create/update device
                device = Device(
                    device_id=f"{adv_host}:{adv_port}",
                    name=payload.get("name", "Unknown")[:64],
                    host=adv_host,
                    port=adv_port,
                    status=status,
                )
                device.protocols = protocols
                self.state.upsert_device(device)
                
            except Exception as e:
                LOG.debug(f"Scan parse error: {e}")


# ══════════════════════════════════════════════════════
#  Qt Event for Transfer Acceptance Dialog
# ══════════════════════════════════════════════════════


class TransferRequestEvent(QEvent):
    """Posted to main window for user to accept/reject incoming transfer.
    
    This is a Qt event that triggers a modal dialog in the GUI thread.
    The handler blocks until the user responds or timeout expires.
    """
    
    _TYPE = QEvent.Type(QEvent.registerEventType())
    
    def __init__(
        self,
        peer_name: str,
        num_files: int,
        total_size: int,
        result: dict,
        ready: threading.Event
    ):
        super().__init__(self._TYPE)
        self.peer_name = peer_name
        self.num_files = num_files
        self.total_size = total_size
        self.result = result
        self.ready = ready
