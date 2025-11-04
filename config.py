import os
import logging
from dataclasses import dataclass, asdict
from logging.handlers import RotatingFileHandler
import json


# Paths and files
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_ROOT, "Data")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "fishare.log")
KEY_FILE = os.path.join(DATA_DIR, "id_ed25519.pem")


class Storage:
    @staticmethod
    def load(defaults: dict) -> dict:
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            return defaults
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**defaults, **data}
        except Exception:
            return defaults

    @staticmethod
    def save(obj) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(obj), f, indent=2)


@dataclass
class Config:
    device_name: str = os.getenv("COMPUTERNAME", "FIshare")[:32]
    download_dir: str = os.path.join(os.path.expanduser("~"), "Downloads")
    allow_incoming: bool = True
    listen_port: int = 49222
    discovery_port: int = 49221

    @staticmethod
    def load():
        return Config(**Storage.load(Config().__dict__))

    def save(self):
        Storage.save(self)


def setup_logging():
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)

    # Avoid duplicate handlers if reconfigured
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(ch)
        root.addHandler(fh)

