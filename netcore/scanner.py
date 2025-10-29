import json, threading, time, logging
from .netutils import make_multicast_socket
from core.state import Device, AppStatus

LOG = logging.getLogger(__name__)

class Scanner:
    def __init__(self, state):
        self.state = state
        self._stop = threading.Event()
        self._sock = make_multicast_socket(state.cfg.discovery_port)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._gc, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _gc(self):
        while not self._stop.is_set():
            now = time.time()
            self.state.devices = {k:v for k,v in self.state.devices.items() if now - v.last_seen < 6}
            time.sleep(2)

    def _listen(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
                payload = json.loads(data.decode('utf-8'))
                if payload.get('type') != 'fishare_adv':
                    continue
                dev = Device(
                    device_id=f"{addr[0]}:{payload['port']}",
                    name=payload.get('name', 'Unknown'),
                    host=addr[0],
                    port=payload['port'],
                    status=AppStatus(payload.get('status', 'available'))
                )
                self.state.upsert_device(dev)
            except Exception as e:
                LOG.debug(f"Scan error: {e}")
