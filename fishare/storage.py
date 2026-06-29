"""Thread-safe JSON persistence for settings, history, quicktexts, muted peers."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import config

_LOCK = threading.RLock()


def _read(path: Path, default: Any) -> Any:
    with _LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default


def _write(path: Path, data: Any) -> None:
    with _LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _append(path: Path, entry: Any) -> None:
    with _LOCK:
        data = _read(path, [])
        data.append(entry)
        _write(path, data)


# ---- Settings ----
def load_settings() -> dict:
    defaults = {
        "device_name": config.default_device_name(),
        "online": True,
        "download_dir": str(config.DEFAULT_DOWNLOAD_DIR),
    }
    return {**defaults, **_read(config.SETTINGS_FILE, {})}


def save_settings(s: dict) -> None:
    _write(config.SETTINGS_FILE, s)


# ---- History ----
def load_history() -> list[dict]:
    return _read(config.HISTORY_FILE, [])


def append_history(entry: dict) -> None:
    _append(config.HISTORY_FILE, entry)


def clear_history() -> None:
    _write(config.HISTORY_FILE, [])


# ---- Quick texts ----
def load_quicktexts() -> list[dict]:
    return _read(config.QUICKTEXTS_FILE, [])


def save_quicktexts(items: list[dict]) -> None:
    _write(config.QUICKTEXTS_FILE, items)


# ---- Muted peers ----
def load_muted() -> set[str]:
    return set(_read(config.MUTED_FILE, []))


def save_muted(muted: set[str]) -> None:
    _write(config.MUTED_FILE, sorted(muted))


# ---- Peer pins ----
def load_pins() -> dict[str, str]:
    raw = _read(config.PINS_FILE, {})
    if not isinstance(raw, dict):
        return {}
    pins: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k and v:
            pins[k] = v
    return pins


def save_pins(pins: dict[str, str]) -> None:
    _write(config.PINS_FILE, dict(sorted(pins.items())))


def check_and_pin(peer_id: str, fingerprint: str) -> tuple[bool, str]:
    if not peer_id or not fingerprint:
        return False, "missing identity"
    with _LOCK:
        pins = load_pins()
        existing = pins.get(peer_id)
        if existing and existing != fingerprint:
            return False, "pinned fingerprint mismatch"
        if not existing:
            pins[peer_id] = fingerprint
            save_pins(pins)
    return True, ""
