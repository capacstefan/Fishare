"""Network layer: discovery (multicast), framed protocol, and file transfers."""

import json
import logging
import os
import socket
import struct
import threading
import time
from typing import List

from history import TransferRecord
from security import Identity, key_agree
from state import AppStatus, Device, TransferStatus

LOG = logging.getLogger(__name__)

MCAST_GRP = "239.255.42.99"


# ── Helpers ─────────────────────────────────────────────


def _get_local_ip() -> str:
    """Best-effort local IPv4 address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _make_mcast_recv(port: int) -> socket.socket:
    """Create a multicast *receive* socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.settimeout(2.0)  # allow clean shutdown
    return s


def _make_mcast_send() -> socket.socket:
    """Create a multicast *send* socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
    return s


# ── Framed protocol ────────────────────────────────────


class Proto:
    """Length-prefixed JSON messages with optional AEAD encryption."""

    HEADER_LEN = 4

    @staticmethod
    def send_json(sock: socket.socket, obj: dict, aead=None):
        data = json.dumps(obj).encode("utf-8")
        if aead:
            data = aead.encrypt(data)
        sock.sendall(struct.pack(">I", len(data)) + data)

    @staticmethod
    def recv_json(sock: socket.socket, aead=None) -> dict:
        header = Proto._recvall(sock, Proto.HEADER_LEN)
        if header is None:
            raise ConnectionError("peer closed connection")
        (length,) = struct.unpack(">I", header)
        if length > 100 * 1024 * 1024:  # sanity: 100 MB frame limit
            raise ValueError(f"frame too large: {length}")
        data = Proto._recvall(sock, length)
        if data is None:
            raise ConnectionError("peer closed mid-frame")
        if aead:
            data = aead.decrypt(data)
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def _recvall(sock: socket.socket, n: int):
        """Read exactly *n* bytes or return None on EOF."""
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None  # peer closed
            buf.extend(chunk)
        return bytes(buf)


# ── Discovery ──────────────────────────────────────────


class Advertiser:
    """Periodically multicast this device's availability."""

    def __init__(self, state):
        self.state = state
        self._stop = threading.Event()
        self._sock = _make_mcast_send()
        self._interval = 1.5

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="advertiser").start()

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass

    def _run(self):
        cfg = self.state.cfg
        while not self._stop.is_set():
            try:
                payload = json.dumps({
                    "type": "fishare_adv",
                    "name": cfg.device_name,
                    "host": _get_local_ip(),
                    "port": cfg.listen_port,
                    "status": self.state.status.value,
                }).encode("utf-8")
                self._sock.sendto(payload, (MCAST_GRP, cfg.discovery_port))
            except Exception as e:
                if not self._stop.is_set():
                    LOG.warning(f"Advertise error: {e}")
            self._stop.wait(self._interval)  # interruptible sleep


class Scanner:
    """Listen for multicast advertisements and maintain device list."""

    def __init__(self, state):
        self.state = state
        self._stop = threading.Event()
        self._sock = _make_mcast_recv(state.cfg.discovery_port)

    def start(self):
        threading.Thread(target=self._listen, daemon=True, name="scanner").start()
        threading.Thread(target=self._gc, daemon=True, name="scanner-gc").start()

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass

    def _gc(self):
        while not self._stop.is_set():
            self.state.prune_devices(ttl_seconds=6.0)
            self._stop.wait(2)

    def _listen(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue

            try:
                payload = json.loads(data.decode("utf-8"))
                if payload.get("type") != "fishare_adv":
                    continue

                local_ip = _get_local_ip()
                adv_host = payload.get("host") or addr[0]
                adv_port = int(payload.get("port", 0))

                if adv_host == local_ip and adv_port == self.state.cfg.listen_port:
                    continue  # skip self

                raw = payload.get("status", "busy")
                status = (
                    AppStatus(raw)
                    if raw in {s.value for s in AppStatus}
                    else AppStatus.BUSY
                )
                self.state.upsert_device(
                    Device(
                        device_id=f"{adv_host}:{adv_port}",
                        name=payload.get("name", "Unknown"),
                        host=adv_host,
                        port=adv_port,
                        status=status,
                    )
                )
            except Exception as e:
                LOG.debug(f"Scan parse error: {e}")


# ── Qt custom event for incoming-transfer dialog ───────

from PyQt6.QtCore import QEvent  # noqa: E402


class TransferRequestEvent(QEvent):
    """Posted to the main window to prompt accept/reject in the GUI thread."""

    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, peer_name: str, num_files: int, total_size: int, result: dict):
        super().__init__(self._TYPE)
        self.peer_name = peer_name
        self.num_files = num_files
        self.total_size = total_size
        self.result = result


# ── Transfer service ───────────────────────────────────


class TransferService:
    """Incoming (server) and outgoing (client) file transfers."""

    MAX_RETRIES = 3
    CHUNK_SIZE = 64 * 1024  # 64 KiB
    ACCEPT_TIMEOUT = 30.0   # seconds to wait for user decision

    def __init__(self, state, ui_root=None, history=None):
        self.state = state
        self.ui_root = ui_root
        self.history = history
        self.identity = Identity()
        self.identity.load_or_create()
        self._stop = threading.Event()
        self._server_sock: socket.socket | None = None
        threading.Thread(
            target=self._server_loop, daemon=True, name="rx-server"
        ).start()

    def stop(self):
        self._stop.set()
        # Close the server socket to unblock accept()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    # ── Accept / reject prompt ──────────────────────────

    def _ask_user_accept(self, peer_name: str, num_files: int, total_size: int) -> bool:
        """Ask user to accept incoming transfer (thread-safe via Qt event)."""
        if self.state.status == AppStatus.BUSY:
            return False
        if not self.ui_root:
            return True

        result: dict = {}
        event = TransferRequestEvent(peer_name, num_files, total_size, result)

        from PyQt6.QtWidgets import QApplication
        QApplication.instance().postEvent(self.ui_root, event)

        # Wait for the GUI thread to set result["decided"]
        deadline = time.time() + self.ACCEPT_TIMEOUT
        while time.time() < deadline:
            if "decided" in result:
                return result.get("accepted", False)
            time.sleep(0.1)

        return False  # timeout → reject

    # ── Receiver (server) ───────────────────────────────

    def _server_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.settimeout(1.0)
        srv.bind(("", self.state.cfg.listen_port))
        srv.listen(16)
        self._server_sock = srv
        LOG.info(f"Receiver listening on port {self.state.cfg.listen_port}")

        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            # Set a generous per-connection timeout so a stalled peer can't
            # block the handler thread forever.
            conn.settimeout(120.0)
            threading.Thread(
                target=self._handle_peer, args=(conn, addr), daemon=True
            ).start()

        srv.close()

    def _handle_peer(self, conn: socket.socket, addr):
        dev_id = addr[0]
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

                accepted = self._ask_user_accept(peer_name, len(files), total)
                Proto.send_json(conn, {"accept": bool(accepted)}, aead)
                if not accepted:
                    LOG.info(f"Transfer rejected from {peer_name}")
                    return

                # ── receive files ──
                self.state.start_transfer(dev_id)
                start_time = time.time()
                received_total = 0

                for _rel in files:
                    hdr = Proto.recv_json(conn, aead)
                    fname = os.path.basename(hdr.get("file", "unnamed"))
                    size = int(hdr.get("size", 0))
                    dest = os.path.join(self.state.cfg.download_dir, fname)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    LOG.info(f"Receiving: {fname} ({size} bytes)")

                    with open(dest, "wb") as f:
                        remaining = size
                        while remaining > 0:
                            msg = Proto.recv_json(conn, aead)
                            data = msg.get("data", "").encode("latin1")
                            f.write(data)
                            received_total += len(data)
                            remaining -= len(data)
                            if total > 0:
                                self.state.update_progress(
                                    dev_id, received_total / total, received_total
                                )

                duration = time.time() - start_time
                self.state.update_progress(dev_id, 1.0, received_total)
                LOG.info(f"Received {len(files)} file(s) from {peer_name}")

                self._record(
                    start_time, "received", peer_name, dev_id,
                    len(files), total, duration, TransferStatus.COMPLETED,
                )

            except Exception as e:
                LOG.error(f"Receive error from {addr}: {e}", exc_info=True)
                self.state.set_transfer_status(dev_id, TransferStatus.ERROR)
            finally:
                # Delay clearing so the UI can flash 100% / error briefly
                threading.Timer(
                    2.0, self.state.clear_progress, args=(dev_id,)
                ).start()

    # ── Sender (client) ─────────────────────────────────

    def send_to(self, device: Device, files: List[str]) -> bool:
        """Send files to a single peer device. Returns True on success."""
        # Pre-validate files exist
        valid_files = [p for p in files if os.path.isfile(p)]
        if not valid_files:
            LOG.warning("No valid files to send.")
            return False

        total = sum(os.path.getsize(p) for p in valid_files)
        start_time = time.time()

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                LOG.info(
                    f"Connecting to {device.name} ({device.host}:{device.port}) "
                    f"attempt {attempt}"
                )
                self.state.start_transfer(device.device_id)

                with socket.create_connection(
                    (device.host, device.port), timeout=10
                ) as sock:
                    sock.settimeout(120.0)
                    aead = key_agree(sock, self.identity.sign)

                    files_rel = [os.path.basename(p) for p in valid_files]
                    Proto.send_json(
                        sock,
                        {
                            "type": "send_request",
                            "files": files_rel,
                            "total": total,
                            "peer_name": self.state.cfg.device_name,
                        },
                        aead,
                    )

                    resp = Proto.recv_json(sock, aead)
                    if not resp.get("accept"):
                        LOG.info(f"{device.name} refused the transfer.")
                        self.state.set_transfer_status(
                            device.device_id, TransferStatus.CANCELED
                        )
                        self.state.update_progress(device.device_id, 1.0, 0)
                        self._record(
                            start_time, "sent", device.name, device.host,
                            len(valid_files), total,
                            time.time() - start_time, TransferStatus.CANCELED,
                            "Transfer rejected by recipient",
                        )
                        threading.Timer(
                            2.0, self.state.clear_progress,
                            args=(device.device_id,),
                        ).start()
                        return False

                    # ── send files ──
                    sent_total = 0
                    for path in valid_files:
                        fname = os.path.basename(path)
                        size = os.path.getsize(path)
                        Proto.send_json(sock, {"file": fname, "size": size}, aead)

                        with open(path, "rb") as f:
                            while True:
                                chunk = f.read(self.CHUNK_SIZE)
                                if not chunk:
                                    break
                                Proto.send_json(
                                    sock, {"data": chunk.decode("latin1")}, aead
                                )
                                sent_total += len(chunk)
                                if total > 0:
                                    self.state.update_progress(
                                        device.device_id,
                                        sent_total / total,
                                        sent_total,
                                    )

                    duration = time.time() - start_time
                    self.state.update_progress(device.device_id, 1.0, sent_total)
                    LOG.info(f"Transfer to {device.name} complete.")

                    self._record(
                        start_time, "sent", device.name, device.host,
                        len(valid_files), total, duration, TransferStatus.COMPLETED,
                    )
                    threading.Timer(
                        2.0, self.state.clear_progress,
                        args=(device.device_id,),
                    ).start()
                    return True

            except Exception as e:
                LOG.warning(f"Send attempt {attempt} failed for {device.name}: {e}")
                if attempt == self.MAX_RETRIES:
                    self.state.set_transfer_status(
                        device.device_id, TransferStatus.ERROR
                    )
                    self._record(
                        start_time, "sent", device.name, device.host,
                        len(valid_files), total,
                        time.time() - start_time, TransferStatus.ERROR, str(e),
                    )
                    threading.Timer(
                        2.0, self.state.clear_progress,
                        args=(device.device_id,),
                    ).start()
                time.sleep(2)

        return False

    # ── History helper ──────────────────────────────────

    def _record(
        self, ts, direction, peer_name, peer_host,
        num_files, total_size, duration, status, error_msg=None,
    ):
        if not self.history:
            return
        self.history.add_record(
            TransferRecord(
                timestamp=ts,
                direction=direction,
                peer_name=peer_name,
                peer_host=peer_host,
                num_files=num_files,
                total_size=total_size,
                duration=duration,
                status=status.value,
                error_msg=error_msg,
            )
        )
