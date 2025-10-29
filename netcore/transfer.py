import os
import socket
import threading
import time
import logging
from typing import List
from .protocol import Proto
from security.crypto_layer import key_agree
from security.identities import Identity
from core.state import AppStatus

LOG = logging.getLogger(__name__)


class TransferService:
    """Handles incoming/outgoing file transfers with retry, progress and restricted mode."""
    MAX_RETRIES = 3
    CHUNK_SIZE = 64 * 1024  # 64 KiB

    def __init__(self, state):
        self.state = state
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

            # dacă e în modul Restricted, refuzăm complet conexiunea
            if self.state.status == AppStatus.RESTRICTED:
                LOG.info(f"Rejected connection from {addr[0]} (restricted mode)")
                conn.close()
                continue

            threading.Thread(target=self._handle_peer, args=(conn, addr), daemon=True).start()

    def _handle_peer(self, conn, addr):
        """Handle an incoming transfer request."""
        with conn:
            try:
                aead = key_agree(conn, self.identity.sign)
                req = Proto.recv_json(conn, aead)

                if req.get("type") != "send_request":
                    LOG.warning(f"Unknown request type from {addr}")
                    return

                files = req["files"]
                total = req["total"]

                from tkinter import messagebox
                accepted = messagebox.askyesno(
                    "Incoming transfer",
                    f"{addr[0]} vrea sa trimita {len(files)} fisiere "
                    f"({total // 1024} KB). Accepti?"
                )

                Proto.send_json(conn, {"accept": bool(accepted)}, aead)
                if not accepted:
                    return

                # Primim fiecare fișier
                for rel in files:
                    fname, size = Proto.recv_json(conn, aead).values()
                    dest_path = os.path.join(self.state.cfg.download_dir, fname)
                    LOG.info(f"Receiving file: {fname} ({size} bytes)")

                    received = 0
                    with open(dest_path, "wb") as f:
                        while received < size:
                            chunk = Proto.recv_json(conn, aead).get("data")
                            if not chunk:
                                break
                            data = chunk.encode("latin1")
                            f.write(data)
                            received += len(data)
                            self.state.update_progress(addr[0], fname, received / size)

                    LOG.info(f"Received {fname} ({received} bytes)")

            except Exception as e:
                LOG.error(f"Receive error from {addr}: {e}", exc_info=True)

    # ---------------------- SENDER ----------------------

    def send_to(self, device, files: List[str]) -> bool:
        """Send files to a single device, with retry logic."""
        total = sum(os.path.getsize(p) for p in files)
        for attempt in range(self.MAX_RETRIES):
            try:
                LOG.info(f"Connecting to {device.name} ({device.host}:{device.port}) attempt {attempt+1}")
                with socket.create_connection((device.host, device.port), timeout=5) as sock:
                    aead = key_agree(sock, self.identity.sign)

                    # Trimit cererea de transfer
                    Proto.send_json(sock, {
                        "type": "send_request",
                        "files": [os.path.basename(p) for p in files],
                        "total": total
                    }, aead)

                    resp = Proto.recv_json(sock, aead)
                    if not resp.get("accept"):
                        LOG.info(f"{device.name} a refuzat transferul.")
                        return False

                    # Trimitem fiecare fișier
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
                                self.state.update_progress(device.device_id, fname, sent / size)

                        LOG.info(f"File {fname} sent ({size} bytes)")

                    LOG.info(f"Transfer to {device.name} complete.")
                    return True

            except Exception as e:
                LOG.warning(f"Send attempt {attempt+1} failed for {device.name}: {e}")
                time.sleep(2)

        LOG.error(f"Transfer failed after {self.MAX_RETRIES} attempts to {device.name}")
        return False
