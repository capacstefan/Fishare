"""Global configuration and app paths (Windows)."""
from __future__ import annotations

import os
import socket
from pathlib import Path

APP_NAME = "P2P LAN Share"
APP_ID = "p2p_lan_share2"

# Network
SERVICE_TYPE = "_p2planshare._tcp.local."
TCP_PORT = 51821
WEB_PORT = 51822
MAX_CONCURRENT_TRANSFERS = 3
SOCKET_TIMEOUT = 30

# Transfer tuning
CHUNK = 1 * 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024
QUICK_TEXT_MAX_CHARS = 500

# Storage (Windows: %APPDATA%\p2p_lan_share2\)
APP_DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_ID
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DATA_DIR / "settings.json"
HISTORY_FILE = APP_DATA_DIR / "history.json"
QUICKTEXTS_FILE = APP_DATA_DIR / "quicktexts.json"
MUTED_FILE = APP_DATA_DIR / "muted.json"
CERT_FILE = APP_DATA_DIR / "cert.pem"
KEY_FILE = APP_DATA_DIR / "key.pem"

# Default download folder
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / APP_NAME
DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def default_device_name() -> str:
    """Return the Windows hostname as a friendly default."""
    try:
        return socket.gethostname()
    except Exception:
        return "Unknown-PC"
