"""Tiny shared helpers — no dependencies on GUI/network."""
from __future__ import annotations

from pathlib import Path


def fmt_size(n: float | int) -> str:
    """Format a byte count as a short human-readable string."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


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
