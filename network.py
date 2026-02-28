"""Network layer: discovery (multicast), framed protocol, and file transfers."""

import json
import logging
import os
import queue
import socket
import struct
import threading
import time
from typing import Dict, List

from history import TransferRecord
from protocols import ProtocolCapabilities, ProtocolSelector
from security import Identity
from state import AppStatus, Device, TransferStatus

LOG = logging.getLogger(__name__)

MCAST_GRP = "239.255.42.99"


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

    def __init__(self, peer_name: str, num_files: int, total_size: int,
                 result: dict, ready: threading.Event):
        super().__init__(self._TYPE)
        self.peer_name = peer_name
        self.num_files = num_files
        self.total_size = total_size
        self.result = result
        self.ready = ready


# ── Transfer service (Phase 1 & 2 integrated) ──────────


class TransferService:
    """Protocol-agnostic file transfer service.

    Starts TCP (and QUIC when available) servers on construction.
    Advertises only protocols whose servers successfully bound.
    Selects the best common protocol for each outgoing transfer.
    """

    MAX_RETRIES = 3
    ACCEPT_TIMEOUT = 30.0

    # Errors where retrying is pointless (and would re-prompt the receiver).
    # Only socket.timeout / TimeoutError are worth retrying.
    _NON_RETRIABLE = (ConnectionResetError, ConnectionRefusedError, PermissionError)

    # Minimum time between progress_cb calls. Throttles state updates and
    # RLock acquisitions to at most 10 per second regardless of chunk rate.
    _PROGRESS_MIN_INTERVAL = 0.1  # seconds

    def __init__(self, state, ui_root=None, history=None):
        self.state = state
        self.ui_root = ui_root
        self.history = history

        identity = Identity()
        identity.load_or_create()
        self.identity = identity

        self.protocol_selector = ProtocolSelector(self.identity, state.cfg)

        # Per-device outgoing queues and worker threads.
        # Each device gets exactly one worker thread that drains its queue
        # sequentially.  Devices are independent, so their workers run in
        # parallel automatically.
        self._queues: Dict[str, queue.Queue] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._worker_lock = threading.Lock()  # guards _queues and _workers

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
        """Ask user to accept an incoming transfer (thread-safe via Qt event).

        Blocks until the user responds or ACCEPT_TIMEOUT seconds elapse.
        Uses threading.Event so the worker thread sleeps with no polling.
        """
        if self.state.status == AppStatus.BUSY:
            return False
        if not self.ui_root:
            return True  # headless / test mode

        result: dict = {}
        ready = threading.Event()
        event = TransferRequestEvent(peer_name, num_files, total_size, result, ready)

        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            return True  # no Qt event loop running

        app.postEvent(self.ui_root, event)

        if ready.wait(timeout=self.ACCEPT_TIMEOUT):
            return result.get("accepted", False)

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

    def send_to(self, device: Device, files: List[str]) -> None:
        """Enqueue a file batch for delivery to *device*. Non-blocking.

        If *device* already has an active transfer, the batch is queued and
        will be sent automatically when the current one finishes.  Multiple
        calls for different devices launch independent worker threads so all
        devices receive files in parallel.
        """
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning("send_to: no valid files — skipping")
            return

        dev_id = device.device_id
        with self._worker_lock:
            if dev_id not in self._queues:
                self._queues[dev_id] = queue.Queue()
            self._queues[dev_id].put((device, valid_files))
            self._ensure_worker_locked(dev_id)

    def _ensure_worker_locked(self, device_id: str) -> None:
        """Start a queue-draining worker for *device_id* if one isn't alive.

        Must be called while holding *self._worker_lock*.
        """
        existing = self._workers.get(device_id)
        if existing and existing.is_alive():
            return
        worker = threading.Thread(
            target=self._run_queue_worker,
            args=(device_id,),
            daemon=True,
            name=f"send-worker-{device_id}",
        )
        self._workers[device_id] = worker
        worker.start()

    def _run_queue_worker(self, device_id: str) -> None:
        """Drain the send queue for one device, one transfer at a time.

        Exits when the queue has been empty for 5 consecutive seconds.
        The 5-second idle window is intentionally generous: it covers the
        case where send_to() puts an item just after the worker drew Empty
        but before acquiring _worker_lock to clean up — the worker will loop
        back, get the item, and never create a broken state.
        """
        q = self._queues.get(device_id)
        if q is None:
            return

        while True:
            try:
                device, files = q.get(timeout=5.0)
            except queue.Empty:
                # Queue appears empty — check under lock before exiting so we
                # don't race with a concurrent send_to() that just enqueued.
                with self._worker_lock:
                    if q.empty():
                        self._queues.pop(device_id, None)
                        self._workers.pop(device_id, None)
                        return
                # Something was added between Empty and the lock — loop again.
                continue

            try:
                self._execute_send(device, files)
            except Exception as e:
                LOG.error(
                    f"Unhandled error in send worker for {device_id}: {e}",
                    exc_info=True,
                )

    def _execute_send(self, device: Device, files: List[str]) -> bool:
        """Execute one transfer to *device*. Blocking. Called only by the worker."""
        # Re-validate files at execution time; they may have been deleted after
        # the user pressed Send.
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning(f"execute_send to {device.name}: all files gone, skipping")
            return False

        # Re-resolve device from live state; the snapshot stored in the queue
        # may be stale (IP could have changed after a reconnect).
        live_device = self.state.devices.get(device.device_id)
        if live_device is None:
            LOG.warning(f"Device {device.name} ({device.device_id}) no longer in state, dropping transfer")
            return False
        device = live_device

        total_size = sum(os.path.getsize(p) for p in valid_files)
        start_time = time.time()

        peer_protocols = getattr(device, "protocols", [])
        protocol = self.protocol_selector.select_for_peer(peer_protocols)
        if not protocol:
            LOG.error(f"No compatible protocol for {device.name}")
            return False

        LOG.info(f"Using {protocol.capabilities.name.value} to send to {device.name}")

        self.state.start_transfer(device.device_id)

        _speed_timer_reset = [False]
        _last_cb_time = [0.0]

        def progress_cb(bytes_sent: int, total: int) -> None:
            if not _speed_timer_reset[0]:
                _speed_timer_reset[0] = True
                self.state.reset_transfer_start(device.device_id)
            now = time.time()
            if total > 0 and (now - _last_cb_time[0]) >= self._PROGRESS_MIN_INTERVAL:
                _last_cb_time[0] = now
                self.state.update_progress(
                    device.device_id, bytes_sent / total, bytes_sent
                )

        last_error = ""
        was_rejected = False

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                target_port = device.port + protocol.capabilities.port_offset
                success = protocol.send_files(
                    device.host,
                    target_port,
                    valid_files,
                    progress_cb,
                    total_size,
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

                # send_files returns False when the peer explicitly rejected.
                was_rejected = True
                last_error = "Transfer rejected by peer"
                LOG.warning(f"Attempt {attempt}: rejected by peer, not retrying")
                break

            except self._NON_RETRIABLE as e:
                last_error = str(e)
                LOG.warning(
                    f"Attempt {attempt}: non-retriable ({type(e).__name__}): {e}"
                )
                break

            except Exception as e:
                last_error = str(e)
                LOG.warning(f"Attempt {attempt} failed: {e}")

            if attempt < self.MAX_RETRIES:
                time.sleep(2)

        final_status = TransferStatus.REJECTED if was_rejected else TransferStatus.ERROR
        self.state.set_transfer_status(device.device_id, final_status)
        self._record(
            start_time, "sent", device.name, device.host,
            len(valid_files), total_size,
            time.time() - start_time,
            final_status, last_error,
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
