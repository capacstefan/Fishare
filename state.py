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
    protocols: list = field(default_factory=list)


@dataclass
class _TransferInfo:
    """All transfer tracking state for one active or recently-finished transfer.

    A single instance replaces the previous five parallel dicts plus the
    _active_transfers set.  Presence in AppState._transfers means active.
    """
    ratio: float = 0.0
    speed_mbps: float = 0.0
    start_time: float = field(default_factory=time.time)
    bytes_transferred: int = 0
    status: TransferStatus = TransferStatus.COMPLETED


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
        # One _TransferInfo per active/recent transfer.
        # Presence in this dict == transfer is active (replaces _active_transfers set).
        self._transfers: Dict[str, _TransferInfo] = {}

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
        """Remove stale devices; never prune a device with an active transfer."""
        with self._lock:
            now = time.time()
            self.devices = {
                k: v for k, v in self.devices.items()
                if now - v.last_seen < ttl_seconds or k in self._transfers
            }
            self.selected_device_ids = [
                d for d in self.selected_device_ids if d in self.devices
            ]

    def cleanup_stale_transfers(self, timeout_seconds: float = 300.0):
        """Safety net: clear transfers that have been silent for 5 minutes."""
        with self._lock:
            now = time.time()
            stale = [
                dev_id for dev_id, info in self._transfers.items()
                if now - info.start_time > timeout_seconds and info.ratio < 0.99
            ]
            for dev_id in stale:
                del self._transfers[dev_id]

    # ── Transfer progress ───────────────────────────────

    def start_transfer(self, device_id: str):
        with self._lock:
            self._transfers[device_id] = _TransferInfo()

    def reset_transfer_start(self, device_id: str):
        """Reset speed timer to now (called on first byte sent, after Accept)."""
        with self._lock:
            info = self._transfers.get(device_id)
            if info:
                info.start_time = time.time()
                info.bytes_transferred = 0

    def is_transfer_active(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._transfers

    def update_progress(self, device_id: str, ratio: float, bytes_transferred: int = 0):
        with self._lock:
            info = self._transfers.get(device_id)
            if not info:
                return
            info.ratio = max(0.0, min(1.0, float(ratio)))
            if bytes_transferred > 0:
                info.bytes_transferred = bytes_transferred
                elapsed = time.time() - info.start_time
                if elapsed > 0.01:
                    info.speed_mbps = (bytes_transferred / (1024 * 1024)) / elapsed

    def get_progress(self, device_id: str) -> float:
        with self._lock:
            info = self._transfers.get(device_id)
            return info.ratio if info else 0.0

    def get_speed(self, device_id: str) -> float:
        with self._lock:
            info = self._transfers.get(device_id)
            return info.speed_mbps if info else 0.0

    def set_transfer_status(self, device_id: str, status: TransferStatus):
        with self._lock:
            info = self._transfers.get(device_id)
            if info:
                info.status = status

    def get_transfer_status(self, device_id: str) -> TransferStatus:
        with self._lock:
            info = self._transfers.get(device_id)
            return info.status if info else TransferStatus.COMPLETED

    def clear_progress(self, device_id: str):
        with self._lock:
            self._transfers.pop(device_id, None)

    def snapshot_progress(self) -> Dict[str, float]:
        with self._lock:
            return {k: v.ratio for k, v in self._transfers.items()}

    def snapshot_devices(self) -> Dict[str, Device]:
        with self._lock:
            return dict(self.devices)

