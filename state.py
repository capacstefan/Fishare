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
        self.progress: Dict[str, Dict[str, float]] = {}

    def set_status(self, status: AppStatus):
        with self._lock:
            self.status = status

    def upsert_device(self, dev: Device):
        with self._lock:
            dev.last_seen = time.time()
            self.devices[dev.device_id] = dev

    def update_progress(self, device_id: str, file: str, ratio: float):
        with self._lock:
            self.progress.setdefault(device_id, {})[file] = ratio

    def get_progress(self, device_id: str):
        with self._lock:
            return self.progress.get(device_id, {})

    def prune_devices(self, ttl_seconds: float = 6.0):
        with self._lock:
            now = time.time()
            existing_ids = set(self.devices.keys())
            self.devices = {k: v for k, v in self.devices.items() if now - v.last_seen < ttl_seconds}

            # Optional: remove progress for pruned devices
            self.progress = {k: v for k, v in self.progress.items() if k in self.devices}
