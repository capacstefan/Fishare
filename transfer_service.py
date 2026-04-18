"""Transfer service with per-device queues and protocol selection.

Manages outgoing transfers with:
- Per-device queue workers (parallel transfers to different devices)
- Sequential processing per device (queued transfers when busy)
- Smart thread management (workers exit when idle)
- Protocol negotiation (QUIC preferred, TCP fallback)
- Automatic retries with exponential backoff
"""

import logging
import os
import queue
import threading
import time
from typing import Dict, List, Optional

from history import TransferHistory, TransferRecord
from protocols import ProtocolSelector
from security import Identity
from state import AppState, AppStatus, Device, TransferStatus

LOG = logging.getLogger(__name__)


class TransferService:
    """Protocol-agnostic file transfer service.
    
    Coordinates incoming and outgoing transfers across multiple protocols.
    Each device gets its own worker thread for independent parallel transfers.
    """
    
    MAX_RETRIES = 3
    ACCEPT_TIMEOUT = 30.0
    PROGRESS_THROTTLE = 0.1  # Min seconds between progress updates
    
    # Errors where retry is pointless
    NON_RETRIABLE = (ConnectionResetError, ConnectionRefusedError, PermissionError)
    
    def __init__(self, state: AppState, ui_root=None, history: TransferHistory = None, known_peers=None):
        self.state = state
        self.ui_root = ui_root
        self.history = history
        self.known_peers = known_peers  # KnownPeers instance (may be None)
        
        # Initialize identity
        identity = Identity()
        identity.load_or_create()
        self.identity = identity
        
        # Protocol selector manages available protocols
        self.protocol_selector = ProtocolSelector(self.identity, state.cfg)
        
        # Per-device outgoing queues and workers
        self._queues: Dict[str, queue.Queue] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._worker_lock = threading.Lock()
        
        # Start protocol servers
        self._servers = []
        self._start_all_servers()
        
        if not self._servers:
            raise RuntimeError(
                "Failed to start any transfer protocol servers! "
                "Cannot receive files."
            )
    
    def _start_all_servers(self):
        """Start servers for all available protocols.
        
        Protocols whose servers fail to start are removed from the selector.
        """
        failed = []
        for protocol in self.protocol_selector.get_protocols():
            name = protocol.capabilities.name.value
            try:
                if protocol.start_server(self._handle_incoming_transfer):
                    self._servers.append(protocol)
                    LOG.info(f"Started {name} server")
                else:
                    LOG.warning(f"Failed to start {name} server")
                    failed.append(protocol)
            except Exception as e:
                LOG.error(f"Exception starting {name} server: {e}", exc_info=True)
                failed.append(protocol)
        
        # Remove failed protocols
        for proto in failed:
            self.protocol_selector.remove_protocol(proto)
    
    def stop(self):
        """Stop all protocol servers."""
        for protocol in self._servers:
            try:
                protocol.stop_server()
            except Exception:
                pass
    
    # ══════════════════════════════════════════════════
    #  Incoming Transfers
    # ══════════════════════════════════════════════════
    
    def _handle_incoming_transfer(self, conn_info, files, total_size):
        """Handle incoming transfer request.
        
        Returns tuple: (accepted, state, history, start_time)
        """
        peer_name = conn_info.get("peer_name", "Unknown")
        dev_id = conn_info.get("dev_id")
        peer_identity_pub = conn_info.get("peer_identity_pub")  # bytes | None
        
        # ── TOFU / key-mismatch check ────────────────────────────────────────
        is_new_device = False
        if self.known_peers is not None and peer_identity_pub is not None:
            tofu_status = self.known_peers.check(dev_id, peer_identity_pub)
            
            if tofu_status == "mismatch":
                LOG.warning(
                    f"SECURITY: identity key mismatch for '{peer_name}' "
                    f"({dev_id}) — blocking transfer (possible MITM)"
                )
                self._notify_key_mismatch(peer_name, dev_id)
                return (False, None, None, time.time())
            
            is_new_device = (tofu_status == "unknown")
        # ────────────────────────────────────────────────────────────────────
        
        # Ask user for acceptance (include new-device note in dialog if needed)
        accepted = self._ask_user_accept(peer_name, len(files), total_size, is_new_device)
        if not accepted:
            LOG.info(f"Transfer from {peer_name} rejected")
            return (False, None, None, time.time())
        
        # User accepted — persist TOFU trust if this was a first-time device
        if is_new_device and self.known_peers is not None and peer_identity_pub is not None:
            self.known_peers.trust(dev_id, peer_identity_pub)
            LOG.info(f"Trusted new device '{peer_name}' ({dev_id})")
        
        # Track transfer
        self.state.start_transfer(dev_id)
        start_time = time.time()
        
        return (True, self.state, self.history, start_time)
    
    def _ask_user_accept(self, peer_name: str, num_files: int, total_size: int, is_new_device: bool = False) -> bool:
        """Prompt user to accept incoming transfer.
        
        Blocks until user responds or timeout expires.
        Uses threading.Event for efficient waiting.
        """
        if self.state.status == AppStatus.BUSY:
            return False
        if not self.ui_root:
            return True  # Headless mode
        
        result: dict = {}
        ready = threading.Event()
        
        from network import TransferRequestEvent
        event = TransferRequestEvent(peer_name, num_files, total_size, result, ready, is_new_device)
        
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            return True
        
        app.postEvent(self.ui_root, event)
        
        if ready.wait(timeout=self.ACCEPT_TIMEOUT):
            return result.get("accepted", False)
        
        LOG.warning(f"Transfer acceptance timeout for {peer_name}")
        return False
    
    def _notify_key_mismatch(self, peer_name: str, device_id: str) -> None:
        """Post a security warning to the UI (fire-and-forget)."""
        if not self.ui_root:
            return
        from network import SecurityWarningEvent
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            msg = (
                f"The identity key of device \u2018{peer_name}\u2019 has changed.\n\n"
                f"This may indicate a man-in-the-middle attack, or the device "
                f"may have simply reinstalled FIshare.\n\n"
                f"The incoming transfer has been blocked.\n\n"
                f"If you trust this device (e.g. they reinstalled the app), "
                f"click \u2018Re-trust\u2019 and ask them to send again."
            )
            app.postEvent(
                self.ui_root,
                SecurityWarningEvent(
                    "\u26a0\ufe0f Security Warning", msg,
                    device_id=device_id,
                    known_peers=self.known_peers,
                ),
            )
    
    # ══════════════════════════════════════════════════
    #  Outgoing Transfers
    # ══════════════════════════════════════════════════
    
    def send_to(self, device: Device, files: List[str]) -> None:
        """Enqueue files for delivery to device (non-blocking).
        
        If device has active transfer, files are queued.
        Multiple devices transfer in parallel via independent workers.
        """
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning("send_to: no valid files")
            return
        
        dev_id = device.device_id
        with self._worker_lock:
            if dev_id not in self._queues:
                self._queues[dev_id] = queue.Queue()
            self._queues[dev_id].put((device, valid_files))
            self._ensure_worker_locked(dev_id)
    
    def _ensure_worker_locked(self, device_id: str) -> None:
        """Start worker for device if not already running.
        
        Must be called while holding _worker_lock.
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
        """Drain send queue for one device.
        
        Processes transfers sequentially for this device.
        Exits after 5 seconds of queue inactivity.
        """
        q = self._queues.get(device_id)
        if q is None:
            return
        
        while True:
            try:
                device, files = q.get(timeout=5.0)
            except queue.Empty:
                # Check if really empty under lock
                with self._worker_lock:
                    if q.empty():
                        self._queues.pop(device_id, None)
                        self._workers.pop(device_id, None)
                        return
                continue
            
            try:
                self._execute_send(device, files)
            except Exception as e:
                LOG.error(f"Unhandled error in send worker for {device_id}: {e}", exc_info=True)
    
    def _execute_send(self, device: Device, files: List[str]) -> bool:
        """Execute one transfer to device (blocking)."""
        # Re-validate files
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning(f"execute_send to {device.name}: all files gone")
            return False
        
        # Re-resolve device from live state
        live_device = self.state.snapshot_devices().get(device.device_id)
        if live_device is None:
            LOG.warning(f"Device {device.name} no longer in state")
            return False
        device = live_device
        
        total_size = sum(os.path.getsize(p) for p in valid_files)
        start_time = time.time()
        
        # Select best protocol
        peer_protocols = getattr(device, "protocols", [])
        protocol = self.protocol_selector.select_for_peer(peer_protocols)
        if not protocol:
            LOG.error(f"No compatible protocol for {device.name}")
            return False
        
        LOG.info(f"Using {protocol.capabilities.name.value} to send to {device.name}")
        
        self.state.start_transfer(device.device_id)
        
        # Progress callback with throttling
        _speed_timer_reset = [False]
        _last_cb_time = [0.0]
        
        def progress_cb(bytes_sent: int, total: int) -> None:
            if not _speed_timer_reset[0]:
                _speed_timer_reset[0] = True
                self.state.reset_transfer_start(device.device_id)
            
            now = time.time()
            if total > 0 and (now - _last_cb_time[0]) >= self.PROGRESS_THROTTLE:
                _last_cb_time[0] = now
                self.state.update_progress(device.device_id, bytes_sent / total, bytes_sent)
        
        # Retry loop
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
                    self._record_transfer(
                        start_time, "sent", device.name, device.host,
                        len(valid_files), total_size, duration,
                        TransferStatus.COMPLETED,
                    )
                    self.state.schedule_clear_progress(device.device_id, 2.0)
                    return True
                
                # Rejected by peer
                was_rejected = True
                last_error = "Transfer rejected by peer"
                LOG.warning(f"Attempt {attempt}: rejected by peer")
                break
            
            except self.NON_RETRIABLE as e:
                last_error = str(e)
                LOG.warning(f"Attempt {attempt}: non-retriable ({type(e).__name__}): {e}")
                break
            
            except Exception as e:
                last_error = str(e)
                LOG.warning(f"Attempt {attempt} failed: {e}")
            
            if attempt < self.MAX_RETRIES:
                time.sleep(2)
        
        # Transfer failed
        final_status = TransferStatus.REJECTED if was_rejected else TransferStatus.ERROR
        self.state.set_transfer_status(device.device_id, final_status)
        self._record_transfer(
            start_time, "sent", device.name, device.host,
            len(valid_files), total_size,
            time.time() - start_time,
            final_status, last_error,
        )
        self.state.schedule_clear_progress(device.device_id, 2.0)
        return False
    
    def _record_transfer(
        self, ts, direction, peer_name, peer_host,
        num_files, total_size, duration, status, error_msg=None,
    ):
        """Record transfer to history."""
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
