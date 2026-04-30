"""Simple JSON-based persistence for settings, history, quick texts, muted peers."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save(path: Path, data: Any) -> None:
    with _lock:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)


# ---------- Settings ----------
def load_settings() -> dict:
    defaults = {
        "device_name": config.default_device_name(),
        "online": True,
        "download_dir": str(config.DEFAULT_DOWNLOAD_DIR),
    }
    return {**defaults, **_load(config.SETTINGS_FILE, {})}


def save_settings(settings: dict) -> None:
    _save(config.SETTINGS_FILE, settings)


# ---------- History ----------
def load_history() -> list[dict]:
    return _load(config.HISTORY_FILE, [])


def append_history(entry: dict) -> None:
    data = load_history()
    data.append(entry)
    _save(config.HISTORY_FILE, data)


def clear_history() -> None:
    _save(config.HISTORY_FILE, [])


# ---------- Quick texts (inbox) ----------
def load_quicktexts() -> list[dict]:
    return _load(config.QUICKTEXTS_FILE, [])


def append_quicktext(entry: dict) -> None:
    data = load_quicktexts()
    data.append(entry)
    _save(config.QUICKTEXTS_FILE, data)


def save_quicktexts(items: list[dict]) -> None:
    _save(config.QUICKTEXTS_FILE, items)


# ---------- Muted peers ----------
def load_muted() -> set[str]:
    return set(_load(config.MUTED_FILE, []))


def save_muted(muted: set[str]) -> None:
    _save(config.MUTED_FILE, sorted(muted))
