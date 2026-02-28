"""Network layer: discovery (multicast), framed protocol, and file transfers."""

import json
import logging
import os
import socket
import struct
import threading
import time
from typing import List

from history import TransferRecord
from protocols import ProtocolSelector
from security import Identity
from state import AppStatus, Device, TransferStatus

LOG = logging.getLogger(__name__)

MCAST_GRP = "239.255.42.99"

# Singleton Identity instance
_identity_instance = None
_identity_lock = threading.Lock()


def get_identity() -> Identity:
    """Get or create singleton Identity instance."""
    global _identity_instance
    if _identity_instance is None:
        with _identity_lock:
            if _identity_instance is None:
                _identity_instance = Identity()
                _identity_instance.load_or_create()
    return _identity_instance


# ── Helpers ─────────────────────────────────────────────


def _get_local_ip() -> str:
    """Best-effort local IPv4 address."""
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
    """Create a multicast *receive* socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.settimeout(2.0)  # allow clean shutdown
    return s


def _make_mcast_send() -> socket.socket:
    """Create a multicast *send* socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
    return s


# ── Discovery ──────────────────────────────────────────


class Advertiser:
    """Periodically multicast this device's availability and protocol support."""

    def __init__(self, state, protocol_selector):
        self.state = state
        self.protocol_selector = protocol_selector
        self._stop = threading.Event()
        self._sock = _make_mcast_send()
        self._interval = 1.5

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="advertiser").start()

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass

    def _run(self):
        cfg = self.state.cfg
        while not self._stop.is_set():
            try:
                # Include protocol capabilities in advertisement
                capabilities = [
                    cap.to_dict() 
                    for cap in self.protocol_selector.get_capabilities()
                ]
                
                payload = json.dumps({
                    "type": "fishare_adv",
                    "name": cfg.device_name,
                    "host": _get_local_ip(),
                    "port": cfg.listen_port,
                    "status": self.state.status.value,
                    "protocols": capabilities,  # Phase 2: advertise protocol support
                }).encode("utf-8")
                self._sock.sendto(payload, (MCAST_GRP, cfg.discovery_port))
            except Exception as e:
                if not self._stop.is_set():
                    LOG.warning(f"Advertise error: {e}")
            self._stop.wait(self._interval)  # interruptible sleep


class Scanner:
    """Listen for multicast advertisements and maintain device list."""

    def __init__(self, state):
        self.state = state
        self._stop = threading.Event()
        self._sock = _make_mcast_recv(state.cfg.discovery_port)

    def start(self):
        threading.Thread(target=self._listen, daemon=True, name="scanner").start()
        threading.Thread(target=self._gc, daemon=True, name="scanner-gc").start()

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass

    def _gc(self):
        """Periodic cleanup of stale devices and transfers."""
        while not self._stop.is_set():
            self.state.prune_devices(ttl_seconds=6.0)
            # Also cleanup stale transfers (safety mechanism)
            self.state.cleanup_stale_transfers(timeout_seconds=300.0)
            self._stop.wait(2)

    def _listen(self):
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

                local_ip = _get_local_ip()
                adv_host = payload.get("host") or addr[0]
                adv_port = int(payload.get("port", 0))

                if adv_host == local_ip and adv_port == self.state.cfg.listen_port:
                    continue  # skip self

                raw = payload.get("status", "busy")
                status = (
                    AppStatus(raw)
                    if raw in {s.value for s in AppStatus}
                    else AppStatus.BUSY
                )
                
                # Phase 2: Parse protocol capabilities
                from protocols import ProtocolCapabilities
                protocol_data = payload.get("protocols", [])
                protocols = []
                for p in protocol_data:
                    try:
                        cap = ProtocolCapabilities.from_dict(p)
                        if cap:
                            protocols.append(cap)
                    except Exception as e:
                        LOG.debug(f"Failed to parse protocol capability: {e}")
                
                device = Device(
                    device_id=f"{adv_host}:{adv_port}",
                    name=payload.get("name", "Unknown"),
                    host=adv_host,
                    port=adv_port,
                    status=status,
                )
                device.protocols = protocols  # Store supported protocols
                self.state.upsert_device(device)
                
            except Exception as e:
                LOG.debug(f"Scan parse error: {e}")


# ── Qt custom event for incoming-transfer dialog ───────

from PyQt6.QtCore import QEvent  # noqa: E402


class TransferRequestEvent(QEvent):
    """Posted to the main window to prompt accept/reject in the GUI thread."""

    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, peer_name: str, num_files: int, total_size: int, result: dict):
        super().__init__(self._TYPE)
        self.peer_name = peer_name
        self.num_files = num_files
        self.total_size = total_size
        self.result = result


# ── Transfer service (Phase 1 & 2 integrated) ──────────


class TransferService:
    """Protocol-agnostic file transfer service.
    
    Phase 1: Uses optimized TCP by default
    Phase 2: Automatically selects QUIC when available
    Phase 3: Ready for C++ crypto replacement
    
    Features:
    - Automatic protocol selection (QUIC → TCP fallback)
    - Multi-protocol server (TCP + QUIC simultaneously)
    - Progress tracking
    - Transfer history
    """

    MAX_RETRIES = 3
    ACCEPT_TIMEOUT = 30.0

    # Errors where retrying is pointless (and would re-prompt the receiver).
    # Only socket.timeout / TimeoutError are worth retrying.
    _NON_RETRIABLE = (ConnectionResetError, ConnectionRefusedError, PermissionError)

    def __init__(self, state, ui_root=None, history=None):
        self.state = state
        self.ui_root = ui_root
        self.history = history
        
        # Use singleton identity
        self.identity = get_identity()
        
        self.protocol_selector = ProtocolSelector(self.identity, state.cfg)
        
        # Start all available protocol servers
        self._servers = []
        self._start_all_servers()
        
        # Validate at least one server started
        if not self._servers:
            raise RuntimeError(
                "Failed to start any transfer protocol servers! "
                "Cannot receive files."
            )

    def _start_all_servers(self):
        """Start servers for all available protocols.

        Any protocol whose server fails to start is removed from the selector
        so it will neither be used for outgoing transfers nor advertised to
        remote peers.
        """
        failed = []
        for protocol in self.protocol_selector.get_protocols():
            name = protocol.capabilities.name.value
            try:
                if protocol.start_server(self._handle_incoming_transfer):
                    self._servers.append(protocol)
                    LOG.info(f"Started {name} server")
                else:
                    LOG.warning(f"Failed to start {name} server — removing from available protocols")
                    failed.append(protocol)
            except Exception as e:
                LOG.error(f"Exception starting {name} server: {e}", exc_info=True)
                failed.append(protocol)

        # Remove protocols whose servers didn't start so they are never
        # advertised or selected for outgoing transfers.
        for proto in failed:
            try:
                self.protocol_selector._protocols.remove(proto)
            except ValueError:
                pass

    def stop(self):
        """Stop all protocol servers."""
        for protocol in self._servers:
            try:
                protocol.stop_server()
            except Exception:
                pass

    # ── Accept / reject prompt ──────────────────────────

    def _ask_user_accept(self, peer_name: str, num_files: int, total_size: int) -> bool:
        """Ask user to accept incoming transfer (thread-safe via Qt event).
        
        Uses event posting to avoid blocking workers with modal dialogs.
        Returns False on timeout or if user is busy.
        """
        if self.state.status == AppStatus.BUSY:
            return False
        if not self.ui_root:
            return True

        result: dict = {}
        event = TransferRequestEvent(peer_name, num_files, total_size, result)

        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            LOG.warning("No QApplication instance, auto-accepting transfer")
            return True
        
        app.postEvent(self.ui_root, event)

        # Wait with shorter intervals for faster response
        deadline = time.time() + self.ACCEPT_TIMEOUT
        while time.time() < deadline:
            if "decided" in result:
                return result.get("accepted", False)
            time.sleep(0.05)  # 50ms instead of 100ms

        LOG.warning(f"Transfer acceptance timeout for {peer_name}")
        return False

    # ── Incoming transfer handler ───────────────────────

    def _handle_incoming_transfer(self, conn_info, files, total_size):
        """Handle incoming transfer request.
        
        Returns tuple: (accepted, state, history, start_time)
        This pattern is cleaner than mutating conn_info.
        """
        peer_name = conn_info.get("peer_name", "Unknown")
        dev_id = conn_info.get("dev_id")
        
        # Ask user
        accepted = self._ask_user_accept(peer_name, len(files), total_size)
        if not accepted:
            LOG.info(f"Transfer from {peer_name} rejected")
            return (False, None, None, time.time())
        
        # Track transfer start
        self.state.start_transfer(dev_id)
        start_time = time.time()
        
        # Return tuple instead of mutating dict
        return (True, self.state, self.history, start_time)

    # ── Outgoing transfer (sender) ──────────────────────

    def send_to(self, device: Device, files: List[str]) -> bool:
        """Send files using best available protocol.
        
        Phase 2: Tries QUIC first, fallbacks to TCP if needed.
        """
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning("No valid files to send")
            return False

        # Ensure device still exists (race condition guard)
        if device.device_id not in self.state.devices:
            LOG.warning(f"Device {device.name} no longer available")
            return False

        # Prevent state corruption from concurrent sends to the same device
        if self.state.is_transfer_active(device.device_id):
            LOG.warning(f"Transfer already in progress to {device.name}, ignoring duplicate")
            return False

        total_size = sum(os.path.getsize(p) for p in valid_files)
        start_time = time.time()

        # Select best protocol for this peer
        peer_protocols = getattr(device, 'protocols', [])
        protocol = self.protocol_selector.select_for_peer(peer_protocols)

        if not protocol:
            LOG.error("No compatible protocol found!")
            return False

        LOG.info(f"Using {protocol.capabilities.name.value} to send to {device.name}")

        # Track progress
        self.state.start_transfer(device.device_id)

        def progress_cb(bytes_sent, total):
            if total > 0:  # Guard: never divide by zero for empty file sets
                self.state.update_progress(
                    device.device_id, bytes_sent / total, bytes_sent
                )

        success = False
        last_error = ""

        # Try to send with retries — but only for transient network errors.
        # ConnectionResetError / ConnectionRefusedError are definitive: the
        # receiver actively closed or is not listening.  Retrying those would
        # just show the accept-dialog again on the remote machine.
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                target_port = device.port + protocol.capabilities.port_offset
                success = protocol.send_files(
                    device.host,
                    target_port,
                    valid_files,
                    progress_cb,
                )

                if success:
                    duration = time.time() - start_time
                    self.state.update_progress(device.device_id, 1.0, total_size)
                    LOG.info(f"Transfer to {device.name} complete in {duration:.1f}s")
                    self._record(
                        start_time, "sent", device.name, device.host,
                        len(valid_files), total_size, duration,
                        TransferStatus.COMPLETED,
                    )
                    threading.Timer(
                        2.0, self.state.clear_progress, args=(device.device_id,)
                    ).start()
                    return True

                # send_files returned False — peer rejected or protocol error.
                # Never retry a rejected transfer: the user already said no.
                last_error = "Transfer rejected by peer"
                LOG.warning(f"Attempt {attempt}: transfer rejected by peer, not retrying")
                break

            except self._NON_RETRIABLE as e:
                last_error = str(e)
                LOG.warning(
                    f"Attempt {attempt}: non-retriable error ({type(e).__name__}): {e} "
                    f"— aborting retry loop"
                )
                break  # pointless and harmful to retry these

            except Exception as e:
                last_error = str(e)
                LOG.warning(f"Attempt {attempt} failed: {e}")

            # Only reach here for genuinely transient errors
            if attempt < self.MAX_RETRIES:
                time.sleep(2)

        # All attempts exhausted — record the failure
        self.state.set_transfer_status(device.device_id, TransferStatus.ERROR)
        self._record(
            start_time, "sent", device.name, device.host,
            len(valid_files), total_size,
            time.time() - start_time,
            TransferStatus.ERROR, last_error,
        )
        threading.Timer(
            2.0, self.state.clear_progress, args=(device.device_id,)
        ).start()
        return False

    # ── History helper ──────────────────────────────────

    def _record(
        self, ts, direction, peer_name, peer_host,
        num_files, total_size, duration, status, error_msg=None,
    ):
        if not self.history:
            return
        self.history.add_record(
            TransferRecord(
                timestamp=ts,
                direction=direction,
                peer_name=peer_name,
                peer_host=peer_host,
                num_files=num_files,
                total_size=total_size,
                duration=duration,
                status=status.value,
                error_msg=error_msg,
            )
        )
