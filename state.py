"""Application state: devices, progress tracking, and status management."""

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Dict, List


class AppStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"


class TransferStatus(str, Enum):
    COMPLETED = "completed"
    ERROR = "error"
    CANCELED = "canceled"


@dataclass
class Device:
    """Represents a discovered peer device on the network."""

    device_id: str
    name: str
    host: str
    port: int
    status: AppStatus
    last_seen: float = field(default_factory=time.time)
    protocols: list = field(default_factory=list)  # Phase 2: supported protocols


class AppState:
    """Thread-safe application state container."""

    def __init__(self, cfg):
        self._lock = RLock()
        self.cfg = cfg
        self.status: AppStatus = (
            AppStatus.AVAILABLE if cfg.allow_incoming else AppStatus.BUSY
        )
        self.devices: Dict[str, Device] = {}
        self.selected_device_ids: List[str] = []
        self.selected_files: List[str] = []
        self.progress: Dict[str, float] = {}
        self.transfer_speeds: Dict[str, float] = {}
        self.transfer_start_times: Dict[str, float] = {}
        self.transfer_bytes: Dict[str, int] = {}
        self.transfer_status: Dict[str, TransferStatus] = {}
        # Set of device_ids currently in active transfer (won't be pruned)
        self._active_transfers: set = set()

    # ── Status ──────────────────────────────────────────

    def set_status(self, status: AppStatus):
        with self._lock:
            self.status = status

    # ── Device management ───────────────────────────────

    def upsert_device(self, dev: Device):
        with self._lock:
            dev.last_seen = time.time()
            self.devices[dev.device_id] = dev

    def prune_devices(self, ttl_seconds: float = 6.0):
        """Remove stale devices but never prune devices with active transfers."""
        with self._lock:
            now = time.time()
            self.devices = {
                k: v
                for k, v in self.devices.items()
                if now - v.last_seen < ttl_seconds or k in self._active_transfers
            }
            # Clean up progress entries whose devices disappeared
            self.progress = {k: v for k, v in self.progress.items() if k in self.devices}
            self.selected_device_ids = [
                d for d in self.selected_device_ids if d in self.devices
            ]
    
    def cleanup_stale_transfers(self, timeout_seconds: float = 300.0):
        """Clean up transfers that have been stale for too long.
        
        This is a safety mechanism for transfers that failed to call clear_progress().
        """
        with self._lock:
            now = time.time()
            stale = []
            for dev_id in self._active_transfers:
                start_time = self.transfer_start_times.get(dev_id)
                if start_time and (now - start_time) > timeout_seconds:
                    progress = self.progress.get(dev_id, 0.0)
                    # Only clean if not at 100% (completed transfers cleanup themselves)
                    if progress < 0.99:
                        stale.append(dev_id)
            
            for dev_id in stale:
                self.clear_progress(dev_id)

    # ── Transfer progress ───────────────────────────────

    def start_transfer(self, device_id: str):
        """Mark the beginning of a transfer."""
        with self._lock:
            self._active_transfers.add(device_id)
            self.transfer_start_times[device_id] = time.time()
            self.transfer_bytes[device_id] = 0
            self.transfer_speeds[device_id] = 0.0
            self.transfer_status[device_id] = TransferStatus.COMPLETED

    def is_transfer_active(self, device_id: str) -> bool:
        """Return True if there is already an ongoing transfer for this device."""
        with self._lock:
            return device_id in self._active_transfers

    def update_progress(self, device_id: str, ratio: float, bytes_transferred: int = 0):
        with self._lock:
            self.progress[device_id] = max(0.0, min(1.0, float(ratio)))
            if bytes_transferred > 0:
                self.transfer_bytes[device_id] = bytes_transferred
                start = self.transfer_start_times.get(device_id)
                if start:
                    elapsed = time.time() - start
                    if elapsed > 0.01:  # avoid division by near-zero
                        self.transfer_speeds[device_id] = (
                            bytes_transferred / (1024 * 1024)
                        ) / elapsed

    def get_progress(self, device_id: str) -> float:
        with self._lock:
            return float(self.progress.get(device_id, 0.0))

    def get_speed(self, device_id: str) -> float:
        """Get transfer speed in MB/s."""
        with self._lock:
            return self.transfer_speeds.get(device_id, 0.0)

    def set_transfer_status(self, device_id: str, status: TransferStatus):
        with self._lock:
            self.transfer_status[device_id] = status

    def get_transfer_status(self, device_id: str) -> TransferStatus:
        with self._lock:
            return self.transfer_status.get(device_id, TransferStatus.COMPLETED)

    def clear_progress(self, device_id: str):
        """Clean up all transfer-related state for a device."""
        with self._lock:
            self._active_transfers.discard(device_id)
            self.progress.pop(device_id, None)
            self.transfer_speeds.pop(device_id, None)
            self.transfer_start_times.pop(device_id, None)
            self.transfer_bytes.pop(device_id, None)
            self.transfer_status.pop(device_id, None)

    def snapshot_progress(self) -> Dict[str, float]:
        """Return a snapshot of progress dict (safe for UI iteration)."""
        with self._lock:
            return dict(self.progress)

    def snapshot_devices(self) -> Dict[str, Device]:
        """Return a snapshot of devices dict (safe for UI iteration)."""
        with self._lock:
            return dict(self.devices)
