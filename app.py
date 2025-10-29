import sys
from utils.logging_config import setup_logging
from settings.config import Config
from core.state import AppState
from netcore.advertiser import Advertiser
from netcore.scanner import Scanner
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

    advertiser.stop()
    scanner.stop()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
