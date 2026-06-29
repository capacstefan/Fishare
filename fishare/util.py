"""Tiny shared helpers (no GUI/network deps)."""
from __future__ import annotations

import socket
from pathlib import Path


def asset_path(name: str) -> Path:
    """Absolute path to a bundled asset under the package's ``assets`` folder.

    Resolves correctly both from source and from a PyInstaller bundle.
    """
    return Path(__file__).resolve().parent / "assets" / name


def fmt_size(n: float | int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_eta(remaining: int, bps: float) -> str:
    if bps <= 0:
        return "--"
    s = int(remaining / bps)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def unique_path(path: Path) -> Path:
    """Return `path`, or `path (N)` if it already exists."""
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    i = 1
    while True:
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
