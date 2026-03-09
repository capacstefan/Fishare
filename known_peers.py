"""TOFU (Trust-On-First-Use) peer identity key store.

Persistently records the Ed25519 public key of every peer that has been
accepted at least once.  On subsequent connections the stored key is compared
with the key presented by the remote side.

Flow:
  check(device_id, key)  → "trusted"  : key matches what we stored → OK
                         → "mismatch" : key differs → potential MITM → REJECT
                         → "unknown"  : first time seeing this device → TOFU prompt
  trust(device_id, key)              : store after user confirms
"""

import base64
import json
import logging
import os
import threading

LOG = logging.getLogger(__name__)


class KnownPeers:
    """Thread-safe persistent store for trusted peer Ed25519 public keys."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._db: dict[str, str] = {}   # device_id → base64 Ed25519 pub bytes
        self._load()

    # ── Public API ──────────────────────────────────────

    def check(self, device_id: str, identity_pub: bytes) -> str:
        """Return 'trusted', 'mismatch', or 'unknown'."""
        with self._lock:
            stored_b64 = self._db.get(device_id)
        if stored_b64 is None:
            return "unknown"
        stored = base64.b64decode(stored_b64)
        return "trusted" if stored == identity_pub else "mismatch"

    def trust(self, device_id: str, identity_pub: bytes) -> None:
        """Record a peer's identity key as trusted and persist to disk."""
        with self._lock:
            self._db[device_id] = base64.b64encode(identity_pub).decode("ascii")
            self._save_locked()

    def forget(self, device_id: str) -> None:
        """Remove a stored key (allows re-TOFU on next contact)."""
        with self._lock:
            if device_id in self._db:
                del self._db[device_id]
                self._save_locked()

    # ── Persistence ─────────────────────────────────────

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._db = data
        except Exception as e:
            LOG.warning(f"Could not load known peers: {e}")
            self._db = {}

    def _save_locked(self) -> None:
        """Must be called while holding self._lock."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._db, f, indent=2)
        except Exception as e:
            LOG.warning(f"Could not save known peers: {e}")
