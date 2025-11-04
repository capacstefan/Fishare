import sys
from config import setup_logging, Config
from core.state import AppState
from network import Advertiser, Scanner
from ui.main_window import FIshareApp

def main():
    setup_logging()
    cfg = Config.load()
    state = AppState(cfg)

    advertiser = Advertiser(state)
    scanner = Scanner(state)
    advertiser.start()
    scanner.start()

    app = FIshareApp(state, advertiser)
    app.mainloop()

    # ensure background services stop cleanly
    try:
        app.transfer.stop()
    except Exception:
        pass
    advertiser.stop()
    scanner.stop()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
