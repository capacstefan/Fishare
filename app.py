"""FIshare — local network file sharing application."""

import sys

from PyQt6.QtWidgets import QApplication

from config import Config, setup_logging
from history import TransferHistory
from main_window import FIshareQtApp
from network import Advertiser, Scanner
from state import AppState


def main():
    setup_logging()
    cfg = Config.load()
    state = AppState(cfg)
    history = TransferHistory()

    # Create protocol selector for advertisement (uses singleton Identity)
    from network import get_identity
    from protocols import ProtocolSelector
    identity = get_identity()
    protocol_selector = ProtocolSelector(identity, cfg)

    advertiser = Advertiser(state, protocol_selector)
    scanner = Scanner(state)
    advertiser.start()
    scanner.start()

    app = QApplication(sys.argv)
    window = FIshareQtApp(state, advertiser, scanner, history)
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
