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

    def __init__(self, state, ui_root=None, history=None):
        self.state = state
        self.ui_root = ui_root  # referință doar pentru context, NU o folosești în thread-urile de rețea
        self.history = history  # Transfer history tracking
        self.identity = Identity()
        self.identity.load_or_create()
        self._stop = threading.Event()
        threading.Thread(target=self._server_loop, daemon=True, name="rx-server").start()

    def stop(self):
        self._stop.set()
    
    def _ask_user_accept(self, peer_name: str, num_files: int, total_size: int) -> bool:
        """Ask user to accept or reject incoming transfer."""
        if self.state.status == AppStatus.BUSY:
            return False
        
        if not self.ui_root:
            return True  # Auto-accept if no UI
        
        # Use Qt event system to show dialog in main thread
        result = {"accepted": False}
        event = _TransferRequestEvent(peer_name, num_files, total_size, result)
        
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().postEvent(self.ui_root, event)
        
        # Wait for user response (with timeout)
        import time
        for _ in range(300):  # 30 seconds timeout
            if "decided" in result:
                return result["accepted"]
            time.sleep(0.1)
        
        return False  # Timeout = reject

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

    def _handle_peer(self, conn, addr):
        with conn:
            try:
                aead = key_agree(conn, self.identity.sign)
                req = Proto.recv_json(conn, aead)

                if req.get("type") != "send_request":
                    LOG.warning(f"Unknown request type from {addr}")
                    return

                files = req.get("files", [])
                total = int(req.get("total", 0))
                peer_name = req.get("peer_name", "Unknown")
                num_files = len(files)

                # Show dialog to user asking for accept/reject
                accepted = self._ask_user_accept(peer_name, num_files, total)

                Proto.send_json(conn, {"accept": bool(accepted)}, aead)
                if not accepted:
                    LOG.info(f"Transfer rejected from {peer_name}")
                    return

                # Receive each file, tracking aggregated progress
                self.state.start_transfer(addr[0])
                start_time = time.time()
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
                            msg = Proto.recv_json(conn, aead)
                            data = msg.get("data", "").encode("latin1")
                            f.write(data)
                            received_total += len(data)
                            remaining -= len(data)
                            if total > 0:
                                self.state.update_progress(addr[0], received_total / total, received_total)

                    LOG.info(f"Received {fname}")

                # finalizează progresul receiver-ului
                duration = time.time() - start_time
                self.state.update_progress(addr[0], 1.0, received_total)
                
                # Add to history
                if self.history:
                    from history import TransferRecord
                    from state import TransferStatus
                    peer_name = req.get("peer_name", "Unknown")
                    self.history.add_record(TransferRecord(
                        timestamp=start_time,
                        direction="received",
                        peer_name=peer_name,
                        peer_host=addr[0],
                        num_files=len(files),
                        total_size=total,
                        duration=duration,
                        status=TransferStatus.COMPLETED.value
                    ))
                
                self.state.clear_progress(addr[0])

            except Exception as e:
                LOG.error(f"Receive error from {addr}: {e}", exc_info=True)
                from state import TransferStatus
                self.state.set_transfer_status(addr[0], TransferStatus.ERROR)
                self.state.clear_progress(addr[0])

    # ---------------------- SENDER ----------------------

    def send_to(self, device, files: List[str]) -> bool:
        total = sum(os.path.getsize(p) for p in files) if files else 0
        start_time = time.time()
        
        for attempt in range(self.MAX_RETRIES):
            try:
                LOG.info(f"Connecting to {device.name} ({device.host}:{device.port}) attempt {attempt+1}")
                self.state.start_transfer(device.device_id)
                
                with socket.create_connection((device.host, device.port), timeout=10) as sock:
                    aead = key_agree(sock, self.identity.sign)

                    # Trimite request cu lista de fișiere și informații despre transfer
                    files_rel = [os.path.basename(p) for p in files]
                    Proto.send_json(sock, {
                        "type": "send_request",
                        "files": files_rel,
                        "total": total,
                        "peer_name": self.state.cfg.device_name
                    }, aead)

                    resp = Proto.recv_json(sock, aead)
                    if not resp.get("accept"):
                        LOG.info(f"{device.name} refused the transfer.")
                        from state import TransferStatus
                        self.state.set_transfer_status(device.device_id, TransferStatus.CANCELED)
                        self.state.update_progress(device.device_id, 1.0, 0)
                        
                        # Add to history as canceled
                        if self.history:
                            from history import TransferRecord
                            self.history.add_record(TransferRecord(
                                timestamp=start_time,
                                direction="sent",
                                peer_name=device.name,
                                peer_host=device.host,
                                num_files=len(files),
                                total_size=total,
                                duration=time.time() - start_time,
                                status=TransferStatus.CANCELED.value,
                                error_msg="Transfer rejected by recipient"
                            ))
                        
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
                                    self.state.update_progress(device.device_id, sent_total / total, sent_total)

                    LOG.info(f"Transfer to {device.name} complete.")
                    duration = time.time() - start_time
                    self.state.update_progress(device.device_id, 1.0, sent_total)
                    
                    # Add to history
                    if self.history:
                        from history import TransferRecord
                        from state import TransferStatus
                        self.history.add_record(TransferRecord(
                            timestamp=start_time,
                            direction="sent",
                            peer_name=device.name,
                            peer_host=device.host,
                            num_files=len(files),
                            total_size=total,
                            duration=duration,
                            status=TransferStatus.COMPLETED.value
                        ))
                    
                    self.state.clear_progress(device.device_id)
                    return True

            except Exception as e:
                LOG.warning(f"Send attempt {attempt+1} failed for {device.name}: {e}")
                if attempt == self.MAX_RETRIES - 1:  # Last attempt
                    duration = time.time() - start_time
                    from state import TransferStatus
                    self.state.set_transfer_status(device.device_id, TransferStatus.ERROR)
                    
                    if self.history:
                        from history import TransferRecord
                        self.history.add_record(TransferRecord(
                            timestamp=start_time,
                            direction="sent",
                            peer_name=device.name,
                            peer_host=device.host,
                            num_files=len(files),
                            total_size=total,
                            duration=duration,
                            status=TransferStatus.ERROR.value,
                            error_msg=str(e)
                        ))
                time.sleep(2)

        LOG.error(f"Transfer failed after {self.MAX_RETRIES} attempts to {device.name}")
        from state import TransferStatus
        self.state.set_transfer_status(device.device_id, TransferStatus.ERROR)
        self.state.clear_progress(device.device_id)
        return False


# Custom Qt Event for transfer request dialog
from PyQt6.QtCore import QEvent

class _TransferRequestEvent(QEvent):
    _TYPE = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, peer_name: str, num_files: int, total_size: int, result: dict):
        super().__init__(self._TYPE)
        self.peer_name = peer_name
        self.num_files = num_files
        self.total_size = total_size
        self.result = result