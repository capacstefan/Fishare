"""Application entry point."""
from __future__ import annotations

import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .gui.main_window import MainWindow
from .gui.theme import apply_theme
from .util import asset_path


def main() -> int:
    if sys.platform == "win32":
        # Make Windows treat this as its own app so the taskbar shows our icon
        # (instead of grouping under / showing the generic python.exe icon).
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Fishare")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Fishare")
    icon = QIcon(str(asset_path("logo.png")))
    if not icon.isNull():
        app.setWindowIcon(icon)
    apply_theme(app)
    win = MainWindow()
    win.show()
    win.notify("Application started")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
