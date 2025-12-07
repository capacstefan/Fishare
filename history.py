"""Transfer history tracking and management."""

import json
import time
from dataclasses import dataclass, asdict
from typing import List, Optional
from threading import RLock

from config import HISTORY_FILE


@dataclass
class TransferRecord:
    """Record of a single file transfer."""
    timestamp: float
    direction: str  # "sent" or "received"
    peer_name: str
    peer_host: str
    num_files: int
    total_size: int  # bytes
    duration: float  # seconds
    status: str  # "completed", "error", or "canceled"
    error_msg: Optional[str] = None
    
    @property
    def speed_mbps(self) -> float:
        """Calculate transfer speed in MB/s."""
        if self.duration > 0 and self.status == "completed":
            return (self.total_size / (1024 * 1024)) / self.duration
        return 0.0
    
    @property
    def timestamp_str(self) -> str:
        """Format timestamp as readable string."""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))


class TransferHistory:
    """Manages transfer history persistence."""
    
    def __init__(self):
        self._lock = RLock()
        self.records: List[TransferRecord] = []
        self.load()
    
    def load(self):
        """Load history from file."""
        try:
            with self._lock:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.records = [TransferRecord(**record) for record in data]
        except (FileNotFoundError, json.JSONDecodeError):
            self.records = []
    
    def save(self):
        """Save history to file."""
        try:
            with self._lock:
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    data = [asdict(record) for record in self.records]
                    json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def add_record(self, record: TransferRecord):
        """Add a new transfer record."""
        with self._lock:
            self.records.insert(0, record)  # Most recent first
            # Keep only last 1000 records
            if len(self.records) > 1000:
                self.records = self.records[:1000]
            self.save()
    
    def delete_record(self, index: int):
        """Delete a specific record by index."""
        with self._lock:
            if 0 <= index < len(self.records):
                self.records.pop(index)
                self.save()
    
    def clear_all(self):
        """Clear all history."""
        with self._lock:
            self.records = []
            self.save()
    
    def get_all(self) -> List[TransferRecord]:
        """Get all records."""
        with self._lock:
            return list(self.records)
