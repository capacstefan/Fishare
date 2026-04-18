"""FIshare — local network file sharing application."""

import logging
import sys

from PyQt6.QtWidgets import QApplication

from config import Config, setup_logging, KNOWN_PEERS_FILE
from history import TransferHistory
from known_peers import KnownPeers
from main_window import FIshareQtApp
from network import Advertiser, Scanner
from transfer_service import TransferService
from state import AppState


def main():
    setup_logging()
    log = logging.getLogger(__name__)
    cfg = Config.load()
    state = AppState(cfg)
    history = TransferHistory()
    known_peers = KnownPeers(KNOWN_PEERS_FILE)

    # Create TransferService before Advertiser so protocol servers start first
    # ui_root is set later once the window exists
    transfer = TransferService(state, ui_root=None, history=history, known_peers=known_peers)

    # Advertiser uses the protocol_selector from TransferService
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
    except KeyboardInterrupt:
        # Allow Ctrl+C from terminal to stop the GUI without a traceback dump.
        log.info("KeyboardInterrupt received, shutting down")
        ret = 0
    finally:
        transfer.stop()
        advertiser.stop()
        scanner.stop()

    sys.exit(ret)


if __name__ == "__main__":
    main()
