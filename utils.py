"""Shared utility helpers used across the application."""


def human_size(b: int) -> str:
    """Return a human-readable file size string (e.g. '4.2 MB')."""
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.2f} TB"
