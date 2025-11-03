import json
import os
from dataclasses import asdict
from settings.paths import CONFIG_FILE, DATA_DIR

class Storage:
    @staticmethod
    def load(defaults: dict) -> dict:
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(CONFIG_FILE):
            return defaults
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**defaults, **data}
        except Exception:
            return defaults

    @staticmethod
    def save(obj) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(obj), f, indent=2)
