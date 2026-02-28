"""TCP file transfer protocol for FIshare.

Transport: TCP with 8 MB socket buffers and TCP_NODELAY.
Framing:    [4-byte big-endian length][ChaCha20-Poly1305 ciphertext]
Control:    JSON frames (handshake, file headers)
Data:       raw binary frames (no JSON serialisation overhead)
"""

import json
import logging
import os
import queue
import socket
import struct
import threading
import time
from typing import Callable, List, Optional

from protocols import ProtocolCapabilities, TCP_CAPABILITIES, TransferProtocol
from security import AEADStream, key_agree
from history import TransferRecord
from state import TransferStatus

LOG = logging.getLogger(__name__)


class TCPProtocol(TransferProtocol):
    """High-performance TCP file transfer.

    - 1 MB chunks: best balance of syscall overhead vs pipelining
    - 8 MB socket buffers: prevents RTT stalls on LAN
    - TCP_NODELAY: no 40 ms Nagle delays
    - recv_into + memoryview: zero-copy receive path
    - Read-ahead queue for files > READ_AHEAD_MIN_SIZE: overlaps disk I/O
      with encrypt+send so neither waits on the other
    """

    # Protocol wire version -- bump whenever the framing changes.
    # Both ends must agree or the connection is rejected cleanly.
    PROTO_VERSION = 2  # v2: raw binary frames for file data

    SOCKET_BUFFER_SIZE       = 8 * 1024 * 1024   # 8 MB kernel socket buffers
    MAX_FRAME_SIZE           = 100 * 1024 * 1024 # sanity cap
    READ_AHEAD_MIN_SIZE      = 8 * 1024 * 1024   # < 8 MB: direct path is faster than thread overhead
    CONNECT_TIMEOUT          = 10.0
    TRANSFER_TIMEOUT         = 60.0               # per-operation inactivity timeout
    MAX_CONCURRENT_TRANSFERS = 8                  # prevent resource exhaustion
    
    def __init__(self, identity, cfg):
        super().__init__(identity, cfg)
        self._capabilities = TCP_CAPABILITIES
        self._server_sock: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._handler_callback: Optional[Callable] = None
        # Limit concurrent connections to prevent resource exhaustion
        self._connection_semaphore = threading.Semaphore(self.MAX_CONCURRENT_TRANSFERS)
    
    def is_available(self) -> bool:
        """TCP is always available."""
        return True
    
    # ── Server (receiver) ───────────────────────────────
    
    def start_server(self, handler_callback) -> bool:
        """Start TCP server for incoming transfers."""
        try:
            self._handler_callback = handler_callback
            self._stop_event.clear()
            
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            self._server_sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, self.SOCKET_BUFFER_SIZE
            )
            
            self._server_sock.settimeout(1.0)
            port = self.cfg.listen_port + self._capabilities.port_offset
            self._server_sock.bind(("", port))
            self._server_sock.listen(16)
            
            self._server_thread = threading.Thread(
                target=self._server_loop, daemon=True, name="tcp-server"
            )
            self._server_thread.start()
            
            LOG.info(f"TCP server listening on port {port}")
            return True
            
        except Exception as e:
            LOG.error(f"Failed to start TCP server: {e}")
            return False
    
    def stop_server(self):
        """Stop TCP server."""
        self._stop_event.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        if self._server_thread:
            self._server_thread.join(timeout=2.0)
    
    def _server_loop(self):
        """Accept incoming connections with concurrency limit."""
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            
            self._optimize_socket(conn)
            
            # Handle in separate thread with concurrency limit
            threading.Thread(
                target=self._handle_connection_wrapper,
                args=(conn, addr),
                daemon=True,
                name=f"tcp-handler-{addr[0]}"
            ).start()
    
    def _handle_connection_wrapper(self, conn: socket.socket, addr):
        """Wrapper to enforce connection limit and ensure socket cleanup."""
        # Try to acquire semaphore (non-blocking - reject if full)
        if not self._connection_semaphore.acquire(blocking=False):
            LOG.warning(f"Connection limit reached, rejecting {addr}")
            try:
                conn.close()
            except Exception:
                pass
            return
        
        try:
            self._handle_connection(conn, addr)
        finally:
            # Always release semaphore and close socket
            self._connection_semaphore.release()
            try:
                conn.close()
            except Exception:
                pass
    
    def _handle_connection(self, conn: socket.socket, addr):
        """Handle a single incoming connection (without 'with' - managed by wrapper)."""
        dev_id = addr[0]
        # Pre-initialize so the except block can safely reference them
        # even if the exception occurs before they are assigned.
        app_state = None
        history_obj = None
        start_time = time.time()
        peer_name = "Unknown"
        files: list = []
        total_size = 0
        current_dest_path = None  # Track file currently being written (for cleanup)

        try:
            # Key agreement (crypto handshake)
            aead = key_agree(conn, self.identity.sign)

            # Receive transfer request
            req = self._recv_json(conn, aead)

            if req.get("type") != "send_request":
                LOG.warning(f"Invalid request from {addr}")
                return

            # Reject if sender uses a different wire protocol version.
            sender_version = req.get("proto_version", 1)  # default 1 for old clients
            if sender_version != self.PROTO_VERSION:
                LOG.error(
                    f"Protocol version mismatch from {addr}: "
                    f"sender={sender_version}, expected={self.PROTO_VERSION}. "
                    f"Update FIshare on both machines."
                )
                self._send_json(conn, {"accept": False, "error": "proto_version_mismatch"}, aead)
                return

            files = req.get("files", [])
            total_size = int(req.get("total", 0))
            peer_name = req.get("peer_name", "Unknown")

            # Ask handler if we should accept
            if not self._handler_callback:
                LOG.error("No handler callback configured!")
                self._send_json(conn, {"accept": False}, aead)
                return

            conn_info = {
                "dev_id": dev_id,
                "peer_name": peer_name,
                "addr": addr,
                "conn": conn,
                "aead": aead,
                "files": files,
                "total_size": total_size,
            }

            # Handler returns tuple: (accepted, state, history, start_time)
            result = self._handler_callback(conn_info, files, total_size)
            if isinstance(result, tuple):
                accepted, app_state, history_obj, start_time = result
            else:
                accepted = result
                app_state = None
                history_obj = None
                start_time = time.time()

            self._send_json(conn, {"accept": accepted}, aead)

            if not accepted:
                LOG.info(f"Transfer from {peer_name} rejected")
                return

            # Receive files with progress tracking
            received_total = 0
            # Hoist path validation base outside the per-file loop
            download_dir_abs = os.path.abspath(self.cfg.download_dir)
            os.makedirs(self.cfg.download_dir, exist_ok=True)
            for _ in files:
                hdr = self._recv_json(conn, aead)
                fname = os.path.basename(hdr.get("file", "unnamed")) or "unnamed"
                fsize = int(hdr.get("size", 0))

                # Security: validate path to prevent traversal
                raw_path = os.path.join(self.cfg.download_dir, fname)
                dest_path = os.path.abspath(raw_path)
                if not dest_path.startswith(download_dir_abs + os.sep) and dest_path != download_dir_abs:
                    LOG.error(f"Path traversal attempt blocked: {fname!r}")
                    return

                # Deduplicate: never silently overwrite an existing file
                dest_path = self._unique_path(dest_path)
                current_dest_path = dest_path  # mark for cleanup on failure

                LOG.info(f"Receiving: {fname} -> {os.path.basename(dest_path)} ({fsize} bytes)")

                # OS write-caching is faster than Python-level buffering
                # (benchmark: default open 20329 MB/s vs 4MB buffer 15578 MB/s)
                with open(dest_path, "wb") as f:
                    remaining = fsize
                    while remaining > 0:
                        data = self._recv_raw(conn, aead)
                        if not data:
                            raise ConnectionError("Received empty data chunk")
                        f.write(data)
                        received_total += len(data)
                        remaining -= len(data)

                        if app_state and total_size > 0:
                            app_state.update_progress(
                                dev_id,
                                received_total / total_size,
                                received_total,
                            )

                current_dest_path = None  # file complete, no cleanup needed

            LOG.info(f"TCP received: {len(files)} files, {received_total} bytes")

            # Record success to history
            if app_state:
                duration = time.time() - start_time
                app_state.update_progress(dev_id, 1.0, received_total)

                if history_obj:
                    history_obj.add_record(TransferRecord(
                        timestamp=start_time,
                        direction="received",
                        peer_name=peer_name,
                        peer_host=addr[0],
                        num_files=len(files),
                        total_size=total_size,
                        duration=duration,
                        status=TransferStatus.COMPLETED.value,
                    ))

                threading.Timer(2.0, app_state.clear_progress, args=(dev_id,)).start()

        except Exception as e:
            LOG.error(f"Connection handler error from {addr}: {e}", exc_info=True)

            # ── Cleanup: remove any partially written file ──────────────
            if current_dest_path and os.path.exists(current_dest_path):
                try:
                    os.remove(current_dest_path)
                    LOG.info(f"Deleted incomplete file: {current_dest_path}")
                except Exception as rm_err:
                    LOG.warning(f"Could not delete incomplete file: {rm_err}")

            # ── Record failure to history ───────────────────────────────
            if app_state and history_obj:
                try:
                    history_obj.add_record(TransferRecord(
                        timestamp=start_time,
                        direction="received",
                        peer_name=peer_name,
                        peer_host=addr[0],
                        num_files=len(files),
                        total_size=total_size,
                        duration=time.time() - start_time,
                        status=TransferStatus.ERROR.value,
                        error_msg=str(e),
                    ))
                except Exception:
                    pass

            # ── Release state immediately — don't wait 5 minutes ───────
            if app_state:
                app_state.clear_progress(dev_id)
    
    # ── Client (sender) ─────────────────────────────────
    
    def send_files(
        self,
        host: str,
        port: int,
        files: List[str],
        progress_callback=None,
        total_size: int = 0,
    ) -> bool:
        """Send files via optimized TCP."""
        valid_files = [f for f in files if os.path.isfile(f)]
        if not valid_files:
            LOG.warning("No valid files to send")
            return False

        if total_size == 0:
            total_size = sum(os.path.getsize(f) for f in valid_files)

        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=self.CONNECT_TIMEOUT)
            self._optimize_socket(sock)

            aead = key_agree(sock, self.identity.sign)

            files_rel = [os.path.basename(f) for f in valid_files]
            self._send_json(sock, {
                "type": "send_request",
                "proto_version": self.PROTO_VERSION,
                "files": files_rel,
                "total": total_size,
                "peer_name": self.cfg.device_name,
            }, aead)

            resp = self._recv_json(sock, aead)
            if not resp.get("accept"):
                reason = resp.get("error", "rejected")
                LOG.info(f"Transfer rejected by peer: {reason}")
                return False

            sent_total = 0
            for file_path in valid_files:
                fname = os.path.basename(file_path)
                fsize = os.path.getsize(file_path)
                chunk_size = self._adaptive_chunk_size(fsize)

                self._send_json(sock, {"file": fname, "size": fsize}, aead)

                if fsize == 0:
                    continue

                if fsize >= self.READ_AHEAD_MIN_SIZE:
                    # Reader thread overlaps disk I/O with encrypt+send.
                    # maxsize=4: up to 4 chunks buffered (max 16 MB for 4 MB chunks).
                    read_q: queue.Queue = queue.Queue(maxsize=4)

                    def _reader(path=file_path, q=read_q, cs=chunk_size):
                        try:
                            with open(path, "rb") as rf:
                                while True:
                                    chunk = rf.read(cs)
                                    if not chunk:
                                        break
                                    q.put(chunk)
                        except Exception as exc:
                            q.put(exc)
                        finally:
                            q.put(None)

                    threading.Thread(target=_reader, daemon=True,
                                     name=f"reader-{fname}").start()

                    while True:
                        item = read_q.get()
                        if item is None:
                            break
                        if isinstance(item, Exception):
                            raise item
                        self._send_raw(sock, item, aead)
                        sent_total += len(item)
                        if progress_callback:
                            progress_callback(sent_total, total_size)
                else:
                    with open(file_path, "rb") as f:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            self._send_raw(sock, chunk, aead)
                            sent_total += len(chunk)
                            if progress_callback:
                                progress_callback(sent_total, total_size)

            LOG.info(f"TCP transfer complete: {len(valid_files)} files, {total_size} bytes")
            return True

        except Exception as e:
            LOG.error(f"TCP send failed: {e}", exc_info=True)
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    
    @staticmethod
    def _adaptive_chunk_size(fsize: int) -> int:
        """Return the best chunk size for a file of *fsize* bytes.

        Tiers:
          < 512 KB  → one single read (fsize bytes)  — avoid extra syscall
          < 10 MB   → 512 KB  — low memory pressure, good for many small files
          < 100 MB  → 1 MB    — standard LAN sweet-spot
          ≥ 100 MB  → 4 MB    — large file: amortise syscall overhead
        """
        if fsize < 512 * 1024:
            return max(fsize, 1)           # one-shot; guard against fsize==0 caller
        if fsize < 10 * 1024 * 1024:
            return 512 * 1024
        if fsize < 100 * 1024 * 1024:
            return 1 * 1024 * 1024
        return 4 * 1024 * 1024

    # Note: receive_files() is intentionally not overridden here.
    # Incoming transfers are handled inline in _handle_connection, which gives
    # direct access to the socket, AEAD stream, and connection metadata.

    @staticmethod
    def _unique_path(path: str) -> str:
        """Return path unchanged if it doesn't exist; otherwise append (1), (2)… until unique.

        Example: 'file.pdf' → 'file(1).pdf' → 'file(2).pdf'
        """
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        n = 1
        while True:
            candidate = f"{base}({n}){ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    # ── Phase 1: Socket optimization ───────────────────
    
    def _optimize_socket(self, sock: socket.socket):
        """Apply TCP performance settings to a connected socket."""
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.SOCKET_BUFFER_SIZE)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.SOCKET_BUFFER_SIZE)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # SO_KEEPALIVE lets the OS detect dead connections without application data,
            # complementing the per-operation timeout set below.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.settimeout(self.TRANSFER_TIMEOUT)
        except Exception as e:
            LOG.warning(f"Failed to optimize socket: {e}")
    
    # ── Raw binary frame helpers (used for file data) ──
    #
    # Frame format:  [4-byte big-endian length][encrypted payload]
    # Identical wire structure to _send_json/_recv_json but skips
    # JSON serialisation — critical for large binary payloads.

    def _send_raw(self, sock: socket.socket, data: bytes, aead: AEADStream):
        """Send a raw binary frame as a single sendall() call.

        Combining the 4-byte length header with the payload into ONE sendall
        avoids sending a standalone tiny header packet (which causes an extra
        recv_into wake-up on the receiver and wastes a TCP segment).
        The 1MB memcpy cost of concatenation is ~0.04ms, negligible vs network.
        """
        payload = aead.encrypt(data) if aead else data
        sock.sendall(struct.pack(">I", len(payload)) + payload)

    def _recv_raw(self, sock: socket.socket, aead: AEADStream) -> bytes:
        """Receive a raw binary frame (no JSON decoding)."""
        header = self._recvall(sock, 4)
        if not header:
            raise ConnectionError("Peer closed connection")
        length = struct.unpack(">I", header)[0]
        if length > self.MAX_FRAME_SIZE:
            raise ValueError(f"Frame too large: {length} bytes")
        payload = self._recvall(sock, length)   # returns bytearray
        if payload is None:
            raise ConnectionError("Peer closed mid-frame")
        # aead.decrypt accepts bytes-like (bytearray), returns bytes
        return aead.decrypt(payload) if aead else bytes(payload)

    # ── Framed JSON protocol (control messages only) ───
    
    def _send_json(self, sock: socket.socket, obj: dict, aead: AEADStream):
        """Send length-prefixed JSON message."""
        data = json.dumps(obj).encode("utf-8")
        if aead:
            data = aead.encrypt(data)
        sock.sendall(struct.pack(">I", len(data)) + data)
    
    def _recv_json(self, sock: socket.socket, aead: AEADStream) -> dict:
        """Receive length-prefixed JSON message."""
        # Read frame length
        header = self._recvall(sock, 4)
        if not header:
            raise ConnectionError("Peer closed connection")
        
        length = struct.unpack(">I", header)[0]
        if length > self.MAX_FRAME_SIZE:
            raise ValueError(f"Frame too large: {length} bytes")
        
        # Read frame data — _recvall returns bytearray, compatible with decrypt
        data = self._recvall(sock, length)
        if data is None:
            raise ConnectionError("Peer closed mid-frame")
        
        if aead:
            data = aead.decrypt(data)  # accepts bytearray, returns bytes
        
        return json.loads(data)
    
    def _recvall(self, sock: socket.socket, n: int):
        """Read exactly n bytes using recv_into + memoryview — zero internal copies.

        Returns a bytearray (not bytes) to avoid an extra full-buffer copy.
        All callers accept bytes-like objects (struct.unpack, aead.decrypt,
        json.loads, f.write) so the bytearray is compatible throughout.

        Benchmark vs old bytearray.extend() + bytes(): 6.5x faster for 4MB frames.
        """
        buf  = bytearray(n)
        view = memoryview(buf)
        pos  = 0
        while pos < n:
            received = sock.recv_into(view[pos:], n - pos)
            if not received:
                return None
            pos += received
        return buf
