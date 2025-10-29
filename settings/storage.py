import json
import os
from dataclasses import asdict

CFG_DIR = os.path.join(os.path.expanduser("~"), ".fishare")
CFG_PATH = os.path.join(CFG_DIR, "config.json")


class Storage:
    @staticmethod
    def load(defaults: dict) -> dict:
        os.makedirs(CFG_DIR, exist_ok=True)
        if not os.path.exists(CFG_PATH):
            return defaults
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**defaults, **data}
        except Exception:
            return defaults

    @staticmethod
    def save(obj) -> None:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(obj), f, indent=2)
