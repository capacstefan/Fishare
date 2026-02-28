"""Application configuration, storage, and logging setup."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_ROOT, "Data")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "fishare.log")
KEY_FILE = os.path.join(DATA_DIR, "id_ed25519.pem")
HISTORY_FILE = os.path.join(DATA_DIR, "transfer_history.json")


def _load_config(defaults: dict) -> dict:
    """Load persisted config from disk, returning *defaults* on any failure."""
    if not os.path.exists(CONFIG_FILE):
        return defaults
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**defaults, **json.load(f)}
    except Exception:
        return defaults


def _save_config(obj) -> None:
    """Persist a dataclass instance to disk as JSON."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(obj), f, indent=2)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to save config: {e}")


@dataclass
class Config:
    """Persistent application configuration."""

    device_name: str = os.getenv("COMPUTERNAME", "FIshare")[:32]
    download_dir: str = os.path.join(os.path.expanduser("~"), "Downloads", "FIshare")
    allow_incoming: bool = True
    listen_port: int = 49222
    discovery_port: int = 49221

    @staticmethod
    def load() -> "Config":
        cfg = Config(**_load_config(Config().__dict__))
        try:
            os.makedirs(cfg.download_dir, exist_ok=True)
        except Exception:
            pass
        return cfg

    def save(self):
        _save_config(self)


def setup_logging():
    """Configure root logger with console + rotating file handlers."""
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Avoid duplicate handlers on repeated calls
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
