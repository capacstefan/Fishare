import json
import threading
import time
import logging
from .netutils import make_multicast_sender, MCAST_GRP
from core.state import AppStatus

LOG = logging.getLogger(__name__)


class Advertiser:
    """Broadcasts (multicast) device availability and status."""

    def __init__(self, state):
        self.state = state
        self._stop = threading.Event()
        self._sock = make_multicast_sender()
        self._interval = 1.5

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        cfg = self.state.cfg
        while not self._stop.is_set():
            if self.state.status == AppStatus.RESTRICTED:
                time.sleep(self._interval)
                continue

            payload = {
                "type": "fishare_adv",
                "name": cfg.device_name,
                "port": cfg.listen_port,
                "status": self.state.status.value,
            }
            try:
                data = json.dumps(payload).encode("utf-8")
                self._sock.sendto(data, (MCAST_GRP, cfg.discovery_port))
            except Exception as e:
                LOG.warning(f"Advertise error: {e}")

            time.sleep(self._interval)
