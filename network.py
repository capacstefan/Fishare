import json
import logging
import socket
import struct
import threading
import time
import os
from typing import List

from state import Device, AppStatus
from security import key_agree, Identity


LOG = logging.getLogger(__name__)

# Multicast group for discovery
MCAST_GRP = "239.255.42.99"


def make_multicast_socket(port: int) -> socket.socket:
    """Multicast receive socket (IPv4)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return s


def make_multicast_sender() -> socket.socket:
    """Multicast send socket (IPv4)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    ttl = struct.pack("b", 2)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    return s


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


class Proto:
    """Framed JSON messages with an optional AEAD layer."""

    HEADER_LEN = 4  # 4 bytes big-endian message length prefix

    @staticmethod
    def send_json(sock: socket.socket, obj: dict, aead=None):
        data = json.dumps(obj).encode("utf-8")
        if aead:
            data = aead.encrypt(data)
        length = struct.pack(">I", len(data))
        sock.sendall(length + data)

    @staticmethod
    def recv_json(sock: socket.socket, aead=None) -> dict:
        header = Proto._recvall(sock, Proto.HEADER_LEN)
        if not header:
            raise ConnectionError("peer closed")
        (length,) = struct.unpack(">I", header)
        data = Proto._recvall(sock, length)
        if aead:
            data = aead.decrypt(data)
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def _recvall(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf


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
            payload = {
                "type": "fishare_adv",
                "name": cfg.device_name,
                "host": get_local_ip(),
                "port": cfg.listen_port,
                "status": self.state.status.value,
            }
            try:
                data = json.dumps(payload).encode("utf-8")
                self._sock.sendto(data, (MCAST_GRP, cfg.discovery_port))
            except Exception as e:
                LOG.warning(f"Advertise error: {e}")

            time.sleep(self._interval)


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
            self.state.prune_devices(ttl_seconds=6.0)
            time.sleep(2)

    def _listen(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
                payload = json.loads(data.decode("utf-8"))
                if payload.get("type") != "fishare_adv":
                    continue

                local_ip = get_local_ip()
                adv_host = payload.get("host") or addr[0]
                adv_port = int(payload.get("port", 0))

                if adv_host == local_ip and adv_port == self.state.cfg.listen_port:
                    continue

                raw_status = payload.get("status", "busy")
                status = AppStatus(raw_status) if raw_status in (s.value for s in AppStatus) else AppStatus.BUSY

                dev = Device(
                    device_id=f"{adv_host}:{adv_port}",
                    name=payload.get("name", "Unknown"),
                    host=adv_host,
                    port=adv_port,
                    status=status,
                )
                self.state.upsert_device(dev)
            except Exception as e:
                LOG.debug(f"Scan error: {e}")


class TransferService:
    """Handles incoming/outgoing file transfers with retry, progress."""

    MAX_RETRIES = 3
    CHUNK_SIZE = 64 * 1024  # 64 KiB

    def __init__(self, state, ui_root=None):
        self.state = state
        self.ui_root = ui_root
        self.identity = Identity()
        self.identity.load_or_create()
        self._stop = threading.Event()
        threading.Thread(target=self._server_loop, daemon=True, name="rx-server").start()

    def stop(self):
        self._stop.set()

    # ---------------------- RECEIVER ----------------------

    def _server_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", self.state.cfg.listen_port))
        s.listen(16)
        LOG.info(f"Receiver listening on port {self.state.cfg.listen_port}")

        while not self._stop.is_set():
            try:
                s.settimeout(1.0)
                conn, addr = s.accept()
            except socket.timeout:
                continue

            threading.Thread(target=self._handle_peer, args=(conn, addr), daemon=True).start()

    def _ask_permission(self, addr, files_count, total_bytes) -> bool:
        """
        Cheamă UI-ul (PyQt) dacă e disponibil. Dacă suntem în BUSY, respingem automat.
        """
        # Busy => respinge automat (cerința 3/4)
        if self.state.status == AppStatus.BUSY:
            return False
        try:
            if self.ui_root and hasattr(self.ui_root, "ask_incoming_confirmation"):
                return bool(self.ui_root.ask_incoming_confirmation(addr[0], files_count, total_bytes))
        except Exception:
            pass
        return True  # fallback simplu

    def _handle_peer(self, conn, addr):
        with conn:
            try:
                aead = key_agree(conn, self.identity.sign)
                req = Proto.recv_json(conn, aead)

                if req.get("type") != "send_request":
                    LOG.warning(f"Unknown request type from {addr}")
                    return

                files = req["files"]
                total = int(req.get("total", 0))

                accepted = self._ask_permission(addr, len(files), total)

                Proto.send_json(conn, {"accept": bool(accepted)}, aead)
                if not accepted:
                    return

                # Receive each file, tracking aggregated progress
                received_total = 0
                for _rel in files:
                    hdr = Proto.recv_json(conn, aead)
                    fname, size = hdr.get("file"), int(hdr.get("size", 0))
                    dest_path = os.path.join(self.state.cfg.download_dir, fname)
                    LOG.info(f"Receiving file: {fname} ({size} bytes)")

                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as f:
                        remaining = size
                        while remaining > 0:
                            chunk_obj = Proto.recv_json(conn, aead)
                            data_s = chunk_obj.get("data")
                            if not data_s:
                                break
                            data = data_s.encode("latin1")
                            f.write(data)
                            received_total += len(data)
                            remaining -= len(data)
                            if total > 0:
                                # pe receiver nu avem device-ul sender înregistrat neapărat;
                                # nu forțăm UI pentru IP/port care nu există în listă
                                self.state.update_progress(addr[0], received_total / total)

                    LOG.info(f"Received {fname}")

                # finalizează progresul receiver-ului
                self.state.update_progress(addr[0], 1.0)
                self.state.clear_progress(addr[0])

            except Exception as e:
                LOG.error(f"Receive error from {addr}: {e}", exc_info=True)
                self.state.clear_progress(addr[0])

    # ---------------------- SENDER ----------------------

    def send_to(self, device, files: List[str]) -> bool:
        # Dacă device-ul este BUSY -> refuz instant (cerința 3)
        if getattr(device, "status", AppStatus.AVAILABLE) == AppStatus.BUSY:
            LOG.info(f"{device.name} este BUSY - refuz automat.")
            try:
                if self.ui_root and hasattr(self.ui_root, "notify_rejected"):
                    self.ui_root.notify_rejected(device.name)
            except Exception:
                pass
            return False

        total = sum(os.path.getsize(p) for p in files) if files else 0
        for attempt in range(self.MAX_RETRIES):
            try:
                LOG.info(f"Connecting to {device.name} ({device.host}:{device.port}) attempt {attempt+1}")
                with socket.create_connection((device.host, device.port), timeout=5) as sock:
                    aead = key_agree(sock, self.identity.sign)

                    # Send transfer request
                    Proto.send_json(
                        sock,
                        {
                            "type": "send_request",
                            "files": [os.path.basename(p) for p in files],
                            "total": total,
                        },
                        aead,
                    )

                    resp = Proto.recv_json(sock, aead)
                    if not resp.get("accept"):
                        LOG.info(f"{device.name} a refuzat transferul.")
                        try:
                            if self.ui_root and hasattr(self.ui_root, "notify_rejected"):
                                self.ui_root.notify_rejected(device.name)
                        except Exception:
                            pass
                        return False

                    # Send each file (agregăm progresul per device)
                    sent_total = 0
                    for path in files:
                        fname = os.path.basename(path)
                        size = os.path.getsize(path)
                        Proto.send_json(sock, {"file": fname, "size": size}, aead)

                        with open(path, "rb") as f:
                            while True:
                                chunk = f.read(self.CHUNK_SIZE)
                                if not chunk:
                                    break
                                Proto.send_json(sock, {"data": chunk.decode("latin1")}, aead)
                                sent_total += len(chunk)
                                if total > 0:
                                    self.state.update_progress(device.device_id, sent_total / total)

                    LOG.info(f"Transfer to {device.name} complete.")
                    self.state.update_progress(device.device_id, 1.0)
                    # curăță progresul după succes
                    self.state.clear_progress(device.device_id)
                    return True

            except Exception as e:
                LOG.warning(f"Send attempt {attempt+1} failed for {device.name}: {e}")
                time.sleep(2)

        LOG.error(f"Transfer failed after {self.MAX_RETRIES} attempts to {device.name}")
        # curăță progresul la eșec
        self.state.clear_progress(device.device_id)
        return False
