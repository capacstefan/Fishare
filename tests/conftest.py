"""
Shared pytest configuration.

This file:
  * Redirects the application's persistent storage (settings, history, cert,
    download dir) to an isolated temp directory BEFORE the fishare
    package is imported, so tests never touch your real user data.
  * Makes the `fishare` package importable whether the tests/ folder
    lives inside the package (current layout) or beside it (recommended
    layout next to your venv / requirements.txt).
  * Provides a couple of small fixtures used by several test modules.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Step 1: isolate APP_DATA_DIR / DEFAULT_DOWNLOAD_DIR *before* import.
# config.py reads APPDATA env var at import time and creates folders under it.
# ---------------------------------------------------------------------------
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="p2p_test_"))
os.environ["APPDATA"] = str(_TEST_ROOT / "appdata")
# Redirect HOME (used by DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / ...)
# Path.home() consults USERPROFILE on Windows, HOME on POSIX.
os.environ["USERPROFILE"] = str(_TEST_ROOT / "home")
os.environ["HOME"] = str(_TEST_ROOT / "home")
(_TEST_ROOT / "appdata").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "home").mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Step 2: make the fishare package importable.
# Layout A (current):  <workspace>/fishare/tests/conftest.py
#                       -> parent of package = workspace's parent
# Layout B (moved):    <project_root>/tests/conftest.py
#                       -> parent of package = project_root
# We just add both candidates; sys.path will dedupe naturally.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
for cand in (_HERE.parent.parent, _HERE.parent):
    s = str(cand)
    if s not in sys.path:
        sys.path.insert(0, s)

# Sanity import: if this fails, tests can't run.
import fishare  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_download_dir(tmp_path: Path) -> Path:
    """Isolated per-test download directory."""
    d = tmp_path / "downloads"
    d.mkdir()
    return d


@pytest.fixture
def free_tcp_port() -> int:
    """Reserve an unused TCP port for tests that need to bind a server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _clean_storage_files():
    """Make sure every test starts with empty persistent storage files."""
    from fishare import config  # noqa: WPS433
    for p in (config.SETTINGS_FILE, config.HISTORY_FILE,
              config.QUICKTEXTS_FILE, config.MUTED_FILE):
        try:
            Path(p).unlink()
        except FileNotFoundError:
            pass
    yield
