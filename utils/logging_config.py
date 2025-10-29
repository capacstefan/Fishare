import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.expanduser("~"), ".fishare")
LOG_PATH = os.path.join(LOG_DIR, "fishare.log")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    fh = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)

    root.addHandler(ch)
    root.addHandler(fh)
