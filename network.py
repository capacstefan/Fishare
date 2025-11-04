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
            if self.state.status == AppStatus.RESTRICTED:
                time.sleep(self._interval)
                continue

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

                dev = Device(
                    device_id=f"{adv_host}:{adv_port}",
                    name=payload.get("name", "Unknown"),
                    host=adv_host,
                    port=adv_port,
                    status=AppStatus(payload.get("status", "available")),
                )
                self.state.upsert_device(dev)
            except Exception as e:
                LOG.debug(f"Scan error: {e}")


class TransferService:
    """Handles incoming/outgoing file transfers with retry, progress and restricted mode."""

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

            if self.state.status == AppStatus.RESTRICTED:
                LOG.info(f"Rejected connection from {addr[0]} (restricted mode)")
                conn.close()
                continue

            threading.Thread(target=self._handle_peer, args=(conn, addr), daemon=True).start()

    def _ask_permission(self, addr, files_count, total_bytes) -> bool:
        # Ensure the messagebox runs on the UI thread
        try:
            from tkinter import messagebox
        except Exception:
            return True

        prompt = f"{addr[0]} doreste sa trimita {files_count} fisiere ({total_bytes // 1024} KB). Accepti?"

        if self.ui_root is None:
            # No UI root provided; best-effort accept
            return messagebox.askyesno("Incoming transfer", prompt)

        from queue import Queue

        q = Queue(maxsize=1)

        def _do():
            try:
                q.put(messagebox.askyesno("Incoming transfer", prompt))
            except Exception:
                q.put(False)

        try:
            self.ui_root.after(0, _do)
            return bool(q.get())
        except Exception:
            return False

    def _handle_peer(self, conn, addr):
        with conn:
            try:
                aead = key_agree(conn, self.identity.sign)
                req = Proto.recv_json(conn, aead)

                if req.get("type") != "send_request":
                    LOG.warning(f"Unknown request type from {addr}")
                    return

                files = req["files"]
                total = req["total"]

                accepted = self._ask_permission(addr, len(files), total)

                Proto.send_json(conn, {"accept": bool(accepted)}, aead)
                if not accepted:
                    return

                # Receive each file
                for _rel in files:
                    hdr = Proto.recv_json(conn, aead)
                    fname, size = hdr.get("file"), int(hdr.get("size", 0))
                    dest_path = os.path.join(self.state.cfg.download_dir, fname)
                    LOG.info(f"Receiving file: {fname} ({size} bytes)")

                    received = 0
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as f:
                        while received < size:
                            chunk_obj = Proto.recv_json(conn, aead)
                            data_s = chunk_obj.get("data")
                            if not data_s:
                                break
                            data = data_s.encode("latin1")
                            f.write(data)
                            received += len(data)
                            self.state.update_progress(addr[0], fname, received / max(1, size))

                    LOG.info(f"Received {fname} ({received} bytes)")

            except Exception as e:
                LOG.error(f"Receive error from {addr}: {e}", exc_info=True)

    # ---------------------- SENDER ----------------------

    def send_to(self, device, files: List[str]) -> bool:
        total = sum(os.path.getsize(p) for p in files)
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
                        return False

                    # Send each file
                    for path in files:
                        fname = os.path.basename(path)
                        size = os.path.getsize(path)
                        Proto.send_json(sock, {"file": fname, "size": size}, aead)

                        with open(path, "rb") as f:
                            sent = 0
                            while True:
                                chunk = f.read(self.CHUNK_SIZE)
                                if not chunk:
                                    break
                                Proto.send_json(sock, {"data": chunk.decode("latin1")}, aead)
                                sent += len(chunk)
                                self.state.update_progress(device.device_id, fname, sent / max(1, size))

                        LOG.info(f"File {fname} sent ({size} bytes)")

                    LOG.info(f"Transfer to {device.name} complete.")
                    return True

            except Exception as e:
                LOG.warning(f"Send attempt {attempt+1} failed for {device.name}: {e}")
                time.sleep(2)

        LOG.error(f"Transfer failed after {self.MAX_RETRIES} attempts to {device.name}")
        return False

