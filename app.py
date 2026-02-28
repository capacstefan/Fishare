"""FIshare — local network file sharing application."""

import sys

from PyQt6.QtWidgets import QApplication

from config import Config, setup_logging
from history import TransferHistory
from main_window import FIshareQtApp
from network import Advertiser, Scanner, TransferService
from state import AppState


def main():
    setup_logging()
    cfg = Config.load()
    state = AppState(cfg)
    history = TransferHistory()

    # Create TransferService *before* Advertiser so we know which protocol
    # servers actually started.  ui_root is set later once the window exists.
    transfer = TransferService(state, ui_root=None, history=history)

    # Advertiser uses the same ProtocolSelector that TransferService already
    # pruned — it only announces protocols whose servers are running.
    advertiser = Advertiser(state, transfer.protocol_selector)
    scanner = Scanner(state)
    advertiser.start()
    scanner.start()

    app = QApplication(sys.argv)
    window = FIshareQtApp(state, advertiser, scanner, history, transfer=transfer)
    window.show()

    ret = 0
    try:
        ret = app.exec()
    finally:
        window.transfer.stop()
        advertiser.stop()
        scanner.stop()

    sys.exit(ret)


if __name__ == "__main__":
    main()
