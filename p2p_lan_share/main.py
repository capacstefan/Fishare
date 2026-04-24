"""Application entry point."""
from __future__ import annotations

import os
import sys

# Force Qt to use software rendering before any Qt import.
# Prevents 0xC0000005 access violations on machines with
# outdated / incompatible GPU drivers (common on Intel iGPU laptops).
os.environ.setdefault("QT_OPENGL", "software")

from PyQt6.QtWidgets import QApplication

from .gui.main_window import MainWindow
from .gui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("P2P LAN Share")
    apply_theme(app)
    win = MainWindow()
    win.show()
    win.notify("Application started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
