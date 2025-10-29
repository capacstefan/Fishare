from dataclasses import dataclass
from .storage import Storage
import os


@dataclass
class Config:
    device_name: str = os.getenv("COMPUTERNAME", "FIshare")[:32]
    download_dir: str = os.path.join(os.path.expanduser("~"), "Downloads")
    allow_incoming: bool = True
    listen_port: int = 49222
    discovery_port: int = 49221

    @staticmethod
    def load():
        return Config(**Storage.load(Config().__dict__))

    def save(self):
        Storage.save(self)
