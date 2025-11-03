import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(APP_ROOT,"Data")
os.makedirs(DATA_DIR,exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "fishare.log")
KEY_FILE = os.path.join(DATA_DIR, "id_ed25519.pem")
