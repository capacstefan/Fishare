"""Unified file transfer implementation for TCP and QUIC protocols.

This module consolidates the TCP and QUIC transfer implementations, extracting
common logic while maintaining protocol-specific optimizations.

Performance features:
- Zero-copy receive (recv_into + memoryview)
- GIL-released C++ engine for file I/O (mandatory)
- Adaptive chunk sizing based on file size
- 8MB socket buffers with TCP_NODELAY
"""

import asyncio
import base64
import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Callable, List, Optional

from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed25519

from history import TransferRecord
from protocols import (
    ProtocolCapabilities,
    ProtocolType,
    QUIC_CAPABILITIES,
    TCP_CAPABILITIES,
    TransferProtocol,
)
from security import AEADStream, key_agree
from state import TransferStatus

LOG = logging.getLogger(__name__)


def _record_receive(
    history_obj,
    start_time: float,
    peer_name: str,
    peer_host: str,
    num_files: int,
    total_size: int,
    duration: float,
    status: TransferStatus,
    error_msg: Optional[str] = None,
) -> None:
    if not history_obj or start_time is None:
        return
    history_obj.add_record(TransferRecord(
        timestamp=start_time,
        direction="received",
        peer_name=peer_name,
        peer_host=peer_host,
        num_files=num_files,
        total_size=total_size,
        duration=duration,
        status=status.value,
        error_msg=error_msg,
    ))

# Import C++ engine (required for file transfers)
try:
    import cpp_engine as _cpp
    # Guard against the cpp_engine/ source directory being silently imported
    # as a Python namespace package instead of the compiled extension.
    if not (callable(getattr(_cpp, "send_file", None)) and
            callable(getattr(_cpp, "recv_file", None))):
        raise ImportError(
            "cpp_engine loaded but send_file/recv_file are missing — "
            "the extension was not compiled yet."
        )
    LOG.info(f"cpp_engine {getattr(_cpp, '__version__', 'unknown')} loaded")
except (ImportError, AttributeError) as e:
    LOG.critical(
        "cpp_engine not available. Build it first: python build_cpp.py\n"
        f"Error: {e}"
    )
    raise RuntimeError(
        "FIshare requires the C++ engine for file transfers. "
        "Please run: python build_cpp.py"
    ) from e

# Try to import aioquic for QUIC support
try:
    from aioquic.asyncio import QuicConnectionProtocol, serve
    from aioquic.asyncio.client import connect
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import StreamDataReceived, HandshakeCompleted
    from aioquic.h3.connection import H3_ALPN
    QUIC_AVAILABLE = True
except ImportError:
    QUIC_AVAILABLE = False
    LOG.info("aioquic not installed, QUIC protocol unavailable")


# ═══════════════════════════════════════════════════════
#  Shared Base Class
# ═══════════════════════════════════════════════════════


class BaseTransferProtocol(TransferProtocol):
    """Base class with shared logic for TCP and QUIC protocols."""
    
    # Protocol constants
    PROTO_VERSION = 3
    MAX_FRAME_SIZE = 100 * 1024 * 1024
    SOCKET_BUFFER_SIZE = 8 * 1024 * 1024
    CONNECT_TIMEOUT = 10.0
    TRANSFER_TIMEOUT = 60.0
    MAX_CONCURRENT = 8
    MAX_FILES_PER_TRANSFER = 1000
    
    def __init__(self, identity, cfg):
        super().__init__(identity, cfg)
        self._handler_callback: Optional[Callable] = None
        self._connection_semaphore = threading.Semaphore(self.MAX_CONCURRENT)
    
    @staticmethod
    def adaptive_chunk_size(fsize: int) -> int:
        """Return optimal chunk size for file of given size.
        
        < 512 KB  → one-shot read
        < 10 MB   → 1 MB
        < 100 MB  → 4 MB
        ≥ 100 MB  → 16 MB
        """
        if fsize < 512 * 1024:
            return max(fsize, 1)
        if fsize < 10 * 1024 * 1024:
            return 1 * 1024 * 1024
        if fsize < 100 * 1024 * 1024:
            return 4 * 1024 * 1024
        return 16 * 1024 * 1024
    
    @staticmethod
    def unique_path(path: str) -> str:
        """Append (1), (2)… until path doesn't exist."""
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        n = 1
        while True:
            candidate = f"{base}({n}){ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    @classmethod
    def validate_request(cls, proto_ver: int, files: list) -> Optional[str]:
        """Return an error string if the request is invalid, else None."""
        if proto_ver != cls.PROTO_VERSION:
            return (
                "Protocol version mismatch "
                f"(peer: {proto_ver}, local: {cls.PROTO_VERSION})"
            )
        if not files:
            return "No files"
        if len(files) > cls.MAX_FILES_PER_TRANSFER:
            return f"Too many files (max {cls.MAX_FILES_PER_TRANSFER})"
        return None


# ═══════════════════════════════════════════════════════
#  TCP Protocol Implementation
# ═══════════════════════════════════════════════════════


class TCPProtocol(BaseTransferProtocol):
    """High-performance TCP file transfer with optional C++ acceleration."""
    
    def __init__(self, identity, cfg):
        super().__init__(identity, cfg)
        self._capabilities = TCP_CAPABILITIES
        self._server_sock: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
    
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
        """Accept incoming connections."""
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
            threading.Thread(
                target=self._handle_connection_wrapper,
                args=(conn, addr),
                daemon=True,
                name=f"tcp-handler-{addr[0]}"
            ).start()
    
    def _handle_connection_wrapper(self, conn: socket.socket, addr):
        """Enforce connection limit and ensure socket cleanup."""
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
            self._connection_semaphore.release()
            try:
                conn.close()
            except Exception:
                pass
    
    def _handle_connection(self, conn: socket.socket, addr):
        """Handle incoming transfer connection."""
        peer_name = "Unknown"
        dev_id = f"{addr[0]}:{self.cfg.listen_port}"
        files = []
        total_size = 0
        current_dest_path = None
        start_time = time.time()
        app_state = None
        history_obj = None
        
        try:
            # Key exchange — always exchanges and verifies Ed25519 identity keys
            aead, peer_identity_pub = key_agree(
                conn, self.identity.sign, self.identity.public_bytes()
            )
            
            # Receive request
            req = self._recv_json(conn, aead)
            
            peer_name = req.get("peer_name", "Unknown")
            proto_ver = req.get("proto_version", 1)
            
            files = req.get("files", [])
            total_size = int(req.get("total", 0))

            err = self.validate_request(proto_ver, files)
            if err:
                self._send_json(conn, {"accept": False, "error": err}, aead)
                return
            
            # Ask handler for acceptance
            if not self._handler_callback:
                self._send_json(conn, {"accept": False, "error": "No handler"}, aead)
                return
            
            conn_info = {
                "dev_id": dev_id,
                "peer_name": peer_name,
                "addr": addr,
                "conn": conn,
                "aead": aead,
                "files": files,
                "total_size": total_size,
                "peer_identity_pub": peer_identity_pub,
            }
            
            result = self._handler_callback(conn_info, files, total_size)
            if isinstance(result, tuple):
                accepted, app_state, history_obj, start_time = result
            else:
                accepted = result
            
            self._send_json(conn, {"accept": accepted}, aead)
            
            if not accepted:
                LOG.info(f"Transfer from {peer_name} rejected")
                return
            
            # Receive files
            received_total = 0
            download_dir_abs = os.path.abspath(self.cfg.download_dir)
            os.makedirs(self.cfg.download_dir, exist_ok=True)
            
            for _ in files:
                hdr = self._recv_json(conn, aead)
                fname = os.path.basename(hdr.get("file", "unnamed")) or "unnamed"
                fsize = int(hdr.get("size", 0))
                
                # Security: validate path
                raw_path = os.path.join(self.cfg.download_dir, fname)
                dest_path = os.path.abspath(raw_path)
                if not dest_path.startswith(download_dir_abs + os.sep) and dest_path != download_dir_abs:
                    LOG.error(f"Path traversal blocked: {fname!r}")
                    return
                
                dest_path = self.unique_path(dest_path)
                current_dest_path = dest_path
                
                LOG.info(f"Receiving: {fname} -> {os.path.basename(dest_path)} ({fsize} bytes)")
                
                if fsize == 0:
                    with open(dest_path, "wb"):
                        pass
                else:
                    # C++ engine handles file receive.
                    # settimeout() puts the Windows SOCKET into non-blocking mode;
                    # C++ ::recv() needs a blocking socket, so switch modes here.
                    def _recv_progress(b_done, b_total):
                        if app_state and total_size > 0:
                            app_state.update_progress(dev_id, b_done / total_size, b_done)
                    
                    conn.setblocking(True)
                    try:
                        aead.recv_nonce = _cpp.recv_file(
                            conn.fileno(), dest_path, fsize,
                            aead.key, aead.recv_nonce,
                            _recv_progress if app_state else None,
                            received_total, total_size
                        )
                    finally:
                        conn.settimeout(self.TRANSFER_TIMEOUT)
                    received_total += fsize
                
                current_dest_path = None
            
            LOG.info(f"TCP received: {len(files)} files, {received_total} bytes")
            
            # Record success
            if app_state:
                duration = time.time() - start_time
                app_state.update_progress(dev_id, 1.0, received_total)
                
                if history_obj:
                    _record_receive(
                        history_obj,
                        start_time,
                        peer_name,
                        addr[0],
                        len(files),
                        total_size,
                        duration,
                        TransferStatus.COMPLETED,
                    )
                
                app_state.schedule_clear_progress(dev_id, 2.0)
        
        except Exception as e:
            LOG.error(f"Connection handler error from {addr}: {e}", exc_info=True)
            
            # Cleanup partial file
            if current_dest_path and os.path.exists(current_dest_path):
                try:
                    os.remove(current_dest_path)
                    LOG.info(f"Deleted incomplete file: {current_dest_path}")
                except Exception as rm_err:
                    LOG.warning(f"Could not delete incomplete file: {rm_err}")
            
            # Record failure
            if app_state and history_obj:
                try:
                    _record_receive(
                        history_obj,
                        start_time,
                        peer_name,
                        addr[0],
                        len(files),
                        total_size,
                        time.time() - start_time,
                        TransferStatus.ERROR,
                        error_msg=str(e),
                    )
                except Exception:
                    pass
            
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
        """Send files via TCP."""
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
            
            # Key exchange — identity key is sent so the receiver can do TOFU
            aead, _ = key_agree(sock, self.identity.sign, self.identity.public_bytes())
            
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
                LOG.info(f"Transfer rejected: {reason}")
                return False
            
            sent_total = 0
            for file_path in valid_files:
                fname = os.path.basename(file_path)
                fsize = os.path.getsize(file_path)
                chunk_size = self.adaptive_chunk_size(fsize)
                
                self._send_json(sock, {"file": fname, "size": fsize}, aead)
                
                if fsize == 0:
                    continue
                
                # C++ engine handles file send.
                # settimeout() puts the Windows SOCKET into non-blocking mode;
                # C++ ::send() needs a blocking socket, so switch modes here.
                sock.setblocking(True)
                try:
                    aead.send_nonce = _cpp.send_file(
                        sock.fileno(), file_path, fsize,
                        aead.key, aead.send_nonce,
                        progress_callback,
                        sent_total, total_size, chunk_size
                    )
                finally:
                    sock.settimeout(self.TRANSFER_TIMEOUT)
                sent_total += fsize
            
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
    
    # ── Socket helpers ──────────────────────────────────
    
    def _optimize_socket(self, sock: socket.socket):
        """Apply TCP performance settings."""
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.SOCKET_BUFFER_SIZE)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.SOCKET_BUFFER_SIZE)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.settimeout(self.TRANSFER_TIMEOUT)
        except Exception as e:
            LOG.warning(f"Failed to optimize socket: {e}")
    
    # ── Framing helpers ─────────────────────────────────
    
    def _send_json(self, sock: socket.socket, obj: dict, aead: AEADStream):
        """Send JSON control message."""
        data = aead.encrypt(json.dumps(obj).encode("utf-8"))
        sock.sendall(struct.pack(">I", len(data)) + data)
    
    def _recv_json(self, sock: socket.socket, aead: AEADStream) -> dict:
        """Receive JSON control message."""
        header = self._recvall(sock, 4)
        if not header:
            raise ConnectionError("Peer closed connection")
        length = struct.unpack(">I", header)[0]
        if length > self.MAX_FRAME_SIZE:
            raise ValueError(f"Frame too large: {length} bytes")
        data = self._recvall(sock, length)
        if data is None:
            raise ConnectionError("Peer closed mid-frame")
        return json.loads(aead.decrypt(data))
    
    def _recvall(self, sock: socket.socket, n: int):
        """Read exactly n bytes using zero-copy recv_into."""
        buf = bytearray(n)
        view = memoryview(buf)
        pos = 0
        while pos < n:
            received = sock.recv_into(view[pos:], n - pos)
            if not received:
                return None
            pos += received
        return buf


# ═══════════════════════════════════════════════════════
#  QUIC Protocol Implementation
# ═══════════════════════════════════════════════════════


class QUICProtocol(BaseTransferProtocol):
    """High-performance QUIC file transfer with 0-RTT and multi-stream support."""
    
    CHUNK_SIZE = 1024 * 1024
    QUIC_TIMEOUT = 8.0
    
    def __init__(self, identity, cfg):
        super().__init__(identity, cfg)
        self._capabilities = QUIC_CAPABILITIES
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    def is_available(self) -> bool:
        """QUIC available when aioquic installed AND certs exist."""
        if not QUIC_AVAILABLE:
            return False
        from pathlib import Path
        return (
            Path("Data/quic_cert.pem").exists()
            and Path("Data/quic_key.pem").exists()
        )
    
    # ── Server (receiver) ───────────────────────────────
    
    def start_server(self, handler_callback) -> bool:
        """Start QUIC server."""
        if not QUIC_AVAILABLE:
            LOG.warning("Cannot start QUIC server: aioquic not installed")
            return False
        
        try:
            self._handler_callback = handler_callback
            self._shutdown = False
            
            _ready = threading.Event()
            _ok = [False]
            
            self._loop = asyncio.new_event_loop()
            
            def run_server():
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(
                    self._start_quic_server(_ready, _ok)
                )
            
            server_thread = threading.Thread(
                target=run_server, daemon=True, name="quic-server"
            )
            server_thread.start()
            
            _ready.wait(timeout=3.0)
            
            if not _ok[0]:
                LOG.warning("QUIC server failed to start")
                return False
            
            port = self.cfg.listen_port + self._capabilities.port_offset
            LOG.info(f"QUIC server started on UDP port {port}")
            return True
        
        except Exception as e:
            LOG.error(f"Failed to start QUIC server: {e}")
            return False
    
    @staticmethod
    def _load_tls_cert():
        """Load TLS cert/key files."""
        from pathlib import Path
        certfile = Path("Data/quic_cert.pem")
        keyfile = Path("Data/quic_key.pem")
        if not certfile.exists() or not keyfile.exists():
            raise FileNotFoundError("QUIC TLS certificates not found")
        return str(certfile), str(keyfile)
    
    async def _start_quic_server(self, ready_event=None, ok_flag=None):
        """Async QUIC server loop."""
        try:
            certfile, keyfile = self._load_tls_cert()
            
            configuration = QuicConfiguration(
                alpn_protocols=H3_ALPN,
                is_client=False,
                max_datagram_frame_size=65536,
            )
            configuration.load_cert_chain(certfile=certfile, keyfile=keyfile)
            
            port = self.cfg.listen_port + self._capabilities.port_offset
            
            server = await serve(
                host="0.0.0.0",
                port=port,
                configuration=configuration,
                create_protocol=lambda *args: FIshareQUICServerProtocol(
                    *args,
                    handler=self._handler_callback,
                    config=self.cfg,
                    identity=self.identity
                ),
            )
            
            if ok_flag is not None:
                ok_flag[0] = True
            if ready_event is not None:
                ready_event.set()
            
            LOG.info(f"QUIC server listening on UDP port {port}")
            
            while not self._shutdown:
                await asyncio.sleep(1)
            
            server.close()
        
        except Exception as e:
            LOG.error(f"QUIC server error: {e}", exc_info=True)
            if ready_event is not None:
                ready_event.set()
    
    def stop_server(self):
        """Stop QUIC server."""
        self._shutdown = True
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
    
    # ── Client (sender) ─────────────────────────────────
    
    def send_files(
        self,
        host: str,
        port: int,
        files: List[str],
        progress_callback=None,
        total_size: int = 0,
    ) -> bool:
        """Send files via QUIC."""
        if not QUIC_AVAILABLE:
            LOG.warning("QUIC not available")
            return False
        
        valid_files = [f for f in files if os.path.isfile(f)]
        if not valid_files:
            return False
        
        if total_size == 0:
            total_size = sum(os.path.getsize(f) for f in valid_files)
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                asyncio.wait_for(
                    self._async_send_files(host, port, valid_files, progress_callback, total_size),
                    timeout=self.QUIC_TIMEOUT + total_size / (10 * 1024 * 1024),  # dynamic timeout
                )
            )
            loop.close()
            return result
        
        except Exception as e:
            LOG.error(f"QUIC send failed: {e}", exc_info=True)
            return False
    
    async def _async_send_files(
        self, host: str, port: int, files: List[str], progress_callback, total_size: int
    ) -> bool:
        """Async QUIC send implementation."""
        try:
            configuration = QuicConfiguration(
                alpn_protocols=H3_ALPN,
                is_client=True,
            )
            
            async with connect(
                host=host,
                port=port,
                configuration=configuration,
                create_protocol=lambda *args: FIshareQUICClientProtocol(
                    *args,
                    identity=self.identity,
                    device_name=self.cfg.device_name,
                    listen_port=self.cfg.listen_port,
                ),
            ) as client:
                await client.send_files(files, progress_callback, total_size)
                return True
        
        except Exception as e:
            LOG.error(f"QUIC async send error: {e}")
            return False


# ═══════════════════════════════════════════════════════
#  QUIC Protocol Handlers
# ═══════════════════════════════════════════════════════


if QUIC_AVAILABLE:
    
    class FIshareQUICServerProtocol(QuicConnectionProtocol):
        """QUIC server protocol for receiving files."""
        
        def __init__(self, *args, handler=None, config=None, identity=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._handler = handler
            self._config = config
            self._identity = identity
            self._files_to_receive = 0
            self._files_received = 0
            self._buffer = {}
            self._stream_states = {}
            self._accepted = False
            self._error = False
            self._error_msg = ""
            self._finalized = False
            # Populated after handler accepts — used for progress & history.
            self._state = None
            self._history = None
            self._start_time = None
            self._dev_id = None
            self._peer_name = "Unknown"
            self._peer_host = ""
            self._total_size = 0
            self._received_bytes = 0
            self._last_progress_ts = 0.0
        
        def quic_event_received(self, event):
            """Handle QUIC events."""
            if isinstance(event, HandshakeCompleted):
                LOG.info("QUIC handshake completed (0-RTT)")
            
            elif isinstance(event, StreamDataReceived):
                stream_id = event.stream_id
                data = event.data
                end_stream = event.end_stream

                if stream_id == 0:  # Control stream
                    if stream_id not in self._buffer:
                        self._buffer[stream_id] = bytearray()
                    self._buffer[stream_id].extend(data)
                    self._handle_control_stream(stream_id, end_stream)
                else:
                    self._handle_file_stream_data(stream_id, data, end_stream)
    
        def _handle_control_stream(self, stream_id, end_stream):
            """Handle control messages on stream 0."""
            if not end_stream:
                return
            
            try:
                data = bytes(self._buffer.get(stream_id, b""))
                self._buffer.pop(stream_id, None)
                msg = json.loads(data.decode("utf-8"))
                
                if msg.get("type") == "send_request":
                    peer_name = msg.get("peer_name", "Unknown")
                    files = msg.get("files", [])
                    total_size = int(msg.get("total", 0))
                    proto_ver = int(msg.get("proto_version", 1))

                    err = BaseTransferProtocol.validate_request(proto_ver, files)
                    if err:
                        self._send_reject(stream_id, error=err)
                        return

                    peer_addr = None
                    peer_host = ""
                    try:
                        if self._stream_transport:
                            peer_addr = self._stream_transport.get_extra_info("peername")
                    except Exception:
                        peer_addr = None
                    if isinstance(peer_addr, tuple) and peer_addr:
                        peer_host = peer_addr[0]

                    peer_port = msg.get("listen_port", msg.get("peer_port"))
                    peer_listen_port = 0
                    try:
                        peer_listen_port = int(peer_port)
                    except (TypeError, ValueError):
                        peer_listen_port = 0
                    if not (1 <= peer_listen_port <= 65535):
                        peer_listen_port = self._config.listen_port

                    dev_id = f"{peer_host or 'unknown'}:{peer_listen_port}"
                    
                    # ── Ed25519 identity check ─────────────────────────
                    identity_key_b64 = msg.get("identity_key")
                    identity_sig_b64 = msg.get("identity_sig")
                    if not identity_key_b64 or not identity_sig_b64:
                        LOG.warning("QUIC: peer sent no identity proof — rejecting")
                        self._send_reject(stream_id, error="Missing identity proof")
                        return
                    try:
                        peer_pub = base64.b64decode(identity_key_b64)
                        peer_sig = base64.b64decode(identity_sig_b64)
                        _ed25519.Ed25519PublicKey.from_public_bytes(peer_pub).verify(
                            peer_sig, peer_name.encode("utf-8")
                        )
                    except Exception as exc:
                        LOG.warning(f"QUIC: identity verification failed: {exc}")
                        self._send_reject(stream_id, error="Identity verification failed")
                        return
                    # ─────────────────────────────────────────────────
                    
                    conn_info = {
                        "dev_id": dev_id,
                        "peer_name": peer_name,
                        "peer_host": peer_host,
                        "addr": peer_addr,
                        "files": files,
                        "total_size": total_size,
                        "peer_identity_pub": peer_pub,
                    }
                    
                    if self._handler:
                        result = self._handler(conn_info, files, total_size)
                        if isinstance(result, tuple):
                            accepted, state, history, start_time = result
                            self._accepted = accepted
                            if accepted:
                                self._state = state
                                self._history = history
                                self._start_time = start_time
                                self._dev_id = dev_id
                                self._peer_name = peer_name
                                self._peer_host = peer_host
                                self._total_size = total_size
                        else:
                            self._accepted = result
                    
                    resp_data = json.dumps({"accept": self._accepted}).encode("utf-8")
                    self._quic.send_stream_data(stream_id + 1, resp_data, end_stream=True)
                    self.transmit()
                    
                    if self._accepted:
                        self._files_to_receive = len(files)
                        LOG.info(f"QUIC transfer accepted from {peer_name}: {len(files)} files")
            
            except Exception as e:
                LOG.error(f"QUIC control stream error: {e}")
        
        def _send_reject(self, stream_id: int, error: Optional[str] = None):
            """Send a rejection response on the control stream."""
            payload = {"accept": False}
            if error:
                payload["error"] = error
            self._accepted = False
            resp_data = json.dumps(payload).encode("utf-8")
            self._quic.send_stream_data(stream_id + 1, resp_data, end_stream=True)
            self.transmit()
        
        def _handle_file_stream_data(self, stream_id, data: bytes, end_stream: bool):
            """Handle streaming file data on a per-stream basis."""
            if not self._accepted:
                return

            state = self._stream_states.get(stream_id)
            if state is None:
                state = {
                    "header": bytearray(),
                    "meta_done": False,
                    "file": None,
                    "dest_path": None,
                    "expected": None,
                    "received": 0,
                    "discard": False,
                    "file_name": "",
                    "error": None,
                }
                self._stream_states[stream_id] = state

            try:
                if not state["meta_done"]:
                    state["header"].extend(data)
                    header_buf = state["header"]
                    idx = header_buf.find(b"\n")
                    if idx == -1:
                        if end_stream:
                            self._mark_stream_error(stream_id, state, "Missing file header")
                            self._finalize_stream(stream_id)
                        return

                    header_line = bytes(header_buf[:idx])
                    rest = bytes(header_buf[idx + 1 :])
                    state["header"].clear()

                    if not self._init_stream_from_header(stream_id, state, header_line):
                        if end_stream:
                            self._finalize_stream(stream_id)
                        return

                    if rest:
                        self._write_stream_bytes(stream_id, state, rest)
                else:
                    if data:
                        self._write_stream_bytes(stream_id, state, data)

                if end_stream:
                    self._finalize_stream(stream_id)

            except Exception as e:
                self._mark_stream_error(stream_id, state, f"Stream error: {e}")
                self._finalize_stream(stream_id)

        def _init_stream_from_header(self, stream_id, state: dict, header_line: bytes) -> bool:
            """Parse header metadata and open destination file."""
            try:
                meta = json.loads(header_line.decode("utf-8"))
            except Exception as e:
                self._mark_stream_error(stream_id, state, f"Invalid header JSON: {e}")
                return False

            fname = os.path.basename(meta.get("file", "unnamed")) or "unnamed"
            try:
                expected = int(meta.get("size", 0))
            except Exception:
                expected = -1

            if expected < 0:
                self._mark_stream_error(stream_id, state, "Invalid file size")
                return False

            download_dir = self._config.download_dir
            os.makedirs(download_dir, exist_ok=True)
            download_dir_abs = os.path.abspath(download_dir)
            dest_path = os.path.abspath(os.path.join(download_dir, fname))
            if not dest_path.startswith(download_dir_abs + os.sep) and dest_path != download_dir_abs:
                self._mark_stream_error(stream_id, state, f"Path traversal blocked: {fname!r}")
                return False

            dest_path = BaseTransferProtocol.unique_path(dest_path)
            state["file_name"] = fname
            state["dest_path"] = dest_path
            state["expected"] = expected
            state["meta_done"] = True

            if expected == 0:
                try:
                    with open(dest_path, "wb"):
                        pass
                except Exception as e:
                    self._mark_stream_error(stream_id, state, f"Create file failed: {e}")
                    return False
                return True

            try:
                state["file"] = open(dest_path, "wb")
            except Exception as e:
                self._mark_stream_error(stream_id, state, f"Open file failed: {e}")
                return False

            return True

        def _write_stream_bytes(self, stream_id, state: dict, data: bytes) -> None:
            if not data or state.get("discard"):
                return

            expected = state.get("expected")
            received = state.get("received", 0)

            if expected == 0:
                self._mark_stream_error(stream_id, state, "Unexpected data for empty file")
                return

            if expected is not None and received >= expected:
                self._mark_stream_error(stream_id, state, "Received more data than expected")
                return

            remaining = expected - received if expected is not None else len(data)
            chunk = data if expected is None or len(data) <= remaining else data[:remaining]

            if len(data) > remaining:
                self._mark_stream_error(stream_id, state, "Received more data than expected")

            f = state.get("file")
            if not f:
                self._mark_stream_error(stream_id, state, "File handle missing")
                return

            f.write(chunk)
            state["received"] = received + len(chunk)
            self._received_bytes += len(chunk)
            self._update_progress()

        def _finalize_stream(self, stream_id: int) -> None:
            state = self._stream_states.pop(stream_id, None)
            if not state:
                return

            f = state.get("file")
            if f:
                try:
                    f.close()
                except Exception:
                    pass

            expected = state.get("expected")
            received = state.get("received", 0)
            had_error = bool(state.get("error"))

            if expected is not None and expected != received and not had_error:
                self._mark_stream_error(
                    stream_id,
                    state,
                    f"Size mismatch (expected {expected}, got {received})",
                )
                had_error = True

            if had_error:
                dest_path = state.get("dest_path")
                if dest_path and os.path.exists(dest_path):
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass

            self._files_received += 1
            if not had_error:
                LOG.info(
                    f"QUIC received: {state.get('file_name', 'unknown')} "
                    f"({received} bytes) [{self._files_received}/{self._files_to_receive}]"
                )

            if self._files_to_receive and self._files_received >= self._files_to_receive:
                self._finalize_transfer()

        def _update_progress(self):
            if not self._state or self._total_size <= 0:
                return
            now = time.time()
            if (now - self._last_progress_ts) < 0.1 and self._received_bytes < self._total_size:
                return
            self._last_progress_ts = now
            self._state.update_progress(
                self._dev_id,
                min(1.0, self._received_bytes / self._total_size),
                self._received_bytes,
            )

        def _finalize_transfer(self):
            if self._finalized:
                return
            self._finalized = True
            if not self._state:
                return

            duration = time.time() - self._start_time if self._start_time else 0.0
            status = TransferStatus.COMPLETED
            error_msg = None

            if self._error:
                status = TransferStatus.ERROR
                error_msg = self._error_msg or "QUIC transfer failed"
                self._state.set_transfer_status(self._dev_id, status)

            self._state.update_progress(self._dev_id, 1.0, self._total_size)

            if self._history and self._start_time is not None:
                _record_receive(
                    self._history,
                    self._start_time,
                    self._peer_name,
                    self._peer_host,
                    self._files_to_receive,
                    self._total_size,
                    duration,
                    status,
                    error_msg=error_msg,
                )

            self._state.schedule_clear_progress(self._dev_id, 2.0)

        def _mark_stream_error(self, stream_id: int, state: dict, message: str) -> None:
            if not state.get("error"):
                state["error"] = message
            if not self._error:
                self._error = True
                self._error_msg = message
            state["discard"] = True
            LOG.error(f"QUIC stream {stream_id}: {message}")
    
    
    class FIshareQUICClientProtocol(QuicConnectionProtocol):
        """QUIC client protocol for sending files."""
        
        def __init__(self, *args, identity=None, device_name=None, listen_port=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._identity = identity
            self._device_name = device_name
            self._listen_port = listen_port
            self._response_received = asyncio.Event()
            self._accepted = False
        
        def quic_event_received(self, event):
            """Handle QUIC events."""
            if isinstance(event, HandshakeCompleted):
                LOG.info("QUIC client handshake completed")
            
            elif isinstance(event, StreamDataReceived):
                # Response from server
                try:
                    data = event.data
                    response = json.loads(data.decode("utf-8"))
                    self._accepted = response.get("accept", False)
                    self._response_received.set()
                except Exception as e:
                    LOG.error(f"QUIC response parse error: {e}")
        
        async def send_files(self, files: List[str], progress_callback, total_size: int):
            """Send files using QUIC streams."""
            files_rel = [os.path.basename(f) for f in files]
            
            # Attach Ed25519 identity proof so the server can verify the sender.
            identity_key_b64 = ""
            identity_sig_b64 = ""
            if self._identity:
                pub = self._identity.public_bytes()
                sig = self._identity.sign(self._device_name.encode("utf-8"))
                identity_key_b64 = base64.b64encode(pub).decode()
                identity_sig_b64 = base64.b64encode(sig).decode()
            
            request = {
                "type": "send_request",
                "proto_version": BaseTransferProtocol.PROTO_VERSION,
                "files": files_rel,
                "total": total_size,
                "peer_name": self._device_name,
                "listen_port": self._listen_port,
                "identity_key": identity_key_b64,
                "identity_sig": identity_sig_b64,
            }
            
            req_data = json.dumps(request).encode("utf-8")
            self._quic.send_stream_data(0, req_data, end_stream=True)
            self.transmit()
            
            # Wait for acceptance
            await asyncio.wait_for(self._response_received.wait(), timeout=5.0)
            
            if not self._accepted:
                LOG.info("QUIC transfer rejected by peer")
                return
            
            # Send each file on separate stream (parallel transfer)
            sent_total = 0
            tasks = []
            
            for file_path in files:
                task = asyncio.create_task(
                    self._send_file(file_path, progress_callback, sent_total, total_size)
                )
                tasks.append(task)
                sent_total += os.path.getsize(file_path)
            
            await asyncio.gather(*tasks)
            LOG.info(f"QUIC transfer complete: {len(files)} files, {total_size} bytes")
        
        async def _send_file(self, file_path: str, progress_callback, offset: int, total_size: int):
            """Send a single file on its own stream, in chunks (no full-file buffering)."""
            stream_id = self._quic.get_next_available_stream_id()
            
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            
            # Send JSON metadata header first.
            meta_data = json.dumps({"file": fname, "size": fsize}).encode("utf-8") + b'\n'
            self._quic.send_stream_data(stream_id, meta_data, end_stream=False)
            self.transmit()
            
            # Stream file in chunks — never load the whole file into memory.
            sent = 0
            pending_chunks = 0
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(QUICProtocol.CHUNK_SIZE)
                    if not chunk:
                        break
                    sent += len(chunk)
                    end = sent >= fsize
                    self._quic.send_stream_data(stream_id, chunk, end_stream=end)
                    pending_chunks += 1
                    if end or pending_chunks >= 4:
                        self.transmit()
                        pending_chunks = 0
                    if progress_callback:
                        progress_callback(offset + sent, total_size)
                    if end or pending_chunks == 0:
                        await asyncio.sleep(0)  # yield to event loop
            
            LOG.info(f"QUIC sent: {fname} ({fsize} bytes)")
