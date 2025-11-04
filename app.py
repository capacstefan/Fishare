import sys
from PyQt6.QtWidgets import QApplication

from config import setup_logging, Config
from state import AppState
from network import Advertiser, Scanner
from main_window import FIshareQtApp


def main():
    setup_logging()
    cfg = Config.load()
    state = AppState(cfg)

    advertiser = Advertiser(state)
    scanner = Scanner(state)
    advertiser.start()
    scanner.start()

    app = QApplication(sys.argv)
    window = FIshareQtApp(state, advertiser, scanner)
    window.show()

    ret = 0
    try:
        ret = app.exec()
    finally:
        try:
            window.transfer.stop()
        except Exception:
            pass
        advertiser.stop()
        scanner.stop()
    sys.exit(ret)


if __name__ == "__main__":
    main()
