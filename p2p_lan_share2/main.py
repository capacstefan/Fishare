"""Application entry point."""
from __future__ import annotations

import sys

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
    raise SystemExit(main())
