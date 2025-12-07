from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
import time
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
    device_id: str
    name: str
    host: str
    port: int
    status: AppStatus
    last_seen: float = field(default_factory=time.time)


class AppState:
    def __init__(self, cfg):
        self._lock = RLock()
        self.cfg = cfg
        self.status: AppStatus = AppStatus.AVAILABLE if cfg.allow_incoming else AppStatus.BUSY
        self.devices: Dict[str, Device] = {}
        self.selected_device_ids: List[str] = []
        self.selected_files: List[str] = []
        self.progress: Dict[str, float] = {}
        self.rejections: List[str] = []  # lista care va fi prelucrată în UI
        self.transfer_speeds: Dict[str, float] = {}  # MB/s per device
        self.transfer_start_times: Dict[str, float] = {}  # start time per device
        self.transfer_bytes: Dict[str, int] = {}  # bytes transferred per device
        self.transfer_status: Dict[str, TransferStatus] = {}  # status per device (for progress bar color)

    def set_status(self, status: AppStatus):
        with self._lock:
            self.status = status

    def upsert_device(self, dev: Device):
        with self._lock:
            dev.last_seen = time.time()
            self.devices[dev.device_id] = dev

    def update_progress(self, device_id: str, ratio: float, bytes_transferred: int = 0):
        with self._lock:
            self.progress[device_id] = max(0.0, min(1.0, float(ratio)))
            if bytes_transferred > 0:
                self.transfer_bytes[device_id] = bytes_transferred
                # Calculate speed
                if device_id in self.transfer_start_times:
                    elapsed = time.time() - self.transfer_start_times[device_id]
                    if elapsed > 0:
                        self.transfer_speeds[device_id] = (bytes_transferred / (1024 * 1024)) / elapsed

    def get_progress(self, device_id: str) -> float:
        with self._lock:
            return float(self.progress.get(device_id, 0.0))
    
    def get_speed(self, device_id: str) -> float:
        """Get transfer speed in MB/s."""
        with self._lock:
            return self.transfer_speeds.get(device_id, 0.0)
    
    def start_transfer(self, device_id: str):
        """Mark transfer start time."""
        with self._lock:
            self.transfer_start_times[device_id] = time.time()
            self.transfer_bytes[device_id] = 0
            self.transfer_speeds[device_id] = 0.0
            self.transfer_status[device_id] = TransferStatus.COMPLETED  # default, will update if error/canceled
    
    def set_transfer_status(self, device_id: str, status: TransferStatus):
        """Set transfer status (completed/error/canceled)."""
        with self._lock:
            self.transfer_status[device_id] = status
    
    def get_transfer_status(self, device_id: str) -> TransferStatus:
        """Get transfer status."""
        with self._lock:
            return self.transfer_status.get(device_id, TransferStatus.COMPLETED)

    def clear_progress(self, device_id: str):
        with self._lock:
            self.progress.pop(device_id, None)
            self.transfer_speeds.pop(device_id, None)
            self.transfer_start_times.pop(device_id, None)
            self.transfer_bytes.pop(device_id, None)
            self.transfer_status.pop(device_id, None)

    def prune_devices(self, ttl_seconds: float = 6.0):
        with self._lock:
            now = time.time()
            self.devices = {k: v for k, v in self.devices.items() if now - v.last_seen < ttl_seconds}
            self.progress = {k: v for k, v in self.progress.items() if k in self.devices}
            self.selected_device_ids = [d for d in self.selected_device_ids if d in self.devices]
