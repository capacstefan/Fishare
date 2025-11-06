from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
import time
from typing import Dict, List


class AppStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"


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
        # Un singur progress per device: 0..1
        self.progress: Dict[str, float] = {}

    def set_status(self, status: AppStatus):
        with self._lock:
            self.status = status

    def upsert_device(self, dev: Device):
        with self._lock:
            dev.last_seen = time.time()
            self.devices[dev.device_id] = dev

    # ----- Progress simplificat (per device) -----
    def update_progress(self, device_id: str, ratio: float):
        with self._lock:
            self.progress[device_id] = max(0.0, min(1.0, float(ratio)))

    def get_progress(self, device_id: str) -> float:
        with self._lock:
            return float(self.progress.get(device_id, 0.0))

    def clear_progress(self, device_id: str):
        with self._lock:
            self.progress.pop(device_id, None)

    def prune_devices(self, ttl_seconds: float = 6.0):
        with self._lock:
            now = time.time()
            # păstrează doar device-urile recente
            self.devices = {k: v for k, v in self.devices.items() if now - v.last_seen < ttl_seconds}
            # curăță progres pentru device-uri dispărute
            self.progress = {k: v for k, v in self.progress.items() if k in self.devices}
            # curăță selecțiile invalide
            self.selected_device_ids = [d for d in self.selected_device_ids if d in self.devices]
