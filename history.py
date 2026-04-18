"""Transfer history tracking and persistence."""

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from threading import RLock
from typing import List, Optional

from config import HISTORY_FILE

LOG = logging.getLogger(__name__)


@dataclass
class TransferRecord:
    """Record of a single file transfer."""

    timestamp: float
    direction: str          # "sent" or "received"
    peer_name: str
    peer_host: str
    num_files: int
    total_size: int         # bytes
    duration: float         # seconds
    status: str             # "completed", "error", "canceled"
    error_msg: Optional[str] = None

    @property
    def speed_mbps(self) -> float:
        if self.duration > 0 and self.status == "completed":
            return (self.total_size / (1024 * 1024)) / self.duration
        return 0.0

    @property
    def timestamp_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))


class TransferHistory:
    """Thread-safe transfer history with JSON persistence."""

    MAX_RECORDS = 1000
    SAVE_INTERVAL = 2.0
    SAVE_BATCH = 5

    def __init__(self):
        self._lock = RLock()
        self.records: deque = deque(maxlen=self.MAX_RECORDS)
        self._dirty = 0
        self._last_save = 0.0
        self.load()

    def load(self):
        """Load history from disk, silently start empty on failure."""
        try:
            with self._lock:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.records = deque(
                        (TransferRecord(**rec) for rec in data),
                        maxlen=self.MAX_RECORDS,
                    )
                self._dirty = 0
                self._last_save = time.time()
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = deque(maxlen=self.MAX_RECORDS)
        except Exception as e:
            LOG.warning(f"Failed to load history: {e}")
            self.records = deque(maxlen=self.MAX_RECORDS)

    def save(self):
        """Persist current history to disk."""
        try:
            with self._lock:
                self._save_locked()
                self._dirty = 0
                self._last_save = time.time()
        except Exception as e:
            LOG.warning(f"Failed to save history: {e}")

    def add_record(self, record: TransferRecord):
        with self._lock:
            self.records.appendleft(record)  # newest first; deque maxlen handles eviction
            self._dirty += 1
            self._maybe_save_locked()

    def delete_record(self, index: int):
        with self._lock:
            if 0 <= index < len(self.records):
                self.records.pop(index)
                self._save_locked()
                self._dirty = 0
                self._last_save = time.time()

    def clear_all(self):
        with self._lock:
            self.records.clear()
            self._save_locked()
            self._dirty = 0
            self._last_save = time.time()

    def get_all(self) -> List[TransferRecord]:
        with self._lock:
            return list(self.records)

    def _maybe_save_locked(self):
        """Save history if batching thresholds are met. Lock must be held."""
        now = time.time()
        if self._dirty >= self.SAVE_BATCH or (now - self._last_save) >= self.SAVE_INTERVAL:
            self._save_locked()
            self._dirty = 0
            self._last_save = now

    def _save_locked(self):
        """Persist history; lock must be held."""
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(
                [asdict(r) for r in self.records],
                f,
                indent=2,
                ensure_ascii=False,
            )
