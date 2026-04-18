"""Protocol abstraction layer for FIshare transfers.

This module provides a clean interface for different transfer protocols,
allowing easy addition of new protocols (e.g., C++ accelerated versions).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

LOG = logging.getLogger(__name__)


class ProtocolType(str, Enum):
    """Supported transfer protocols."""
    TCP = "tcp"
    QUIC = "quic"


@dataclass
class ProtocolCapabilities:
    """Describes a protocol for advertisement and selection."""

    name: ProtocolType
    version: str
    port_offset: int  # added to base listen_port

    def to_dict(self) -> dict:
        return {"name": self.name.value, "version": self.version}

    @staticmethod
    def from_dict(data: dict) -> Optional["ProtocolCapabilities"]:
        name = data.get("name", "tcp")
        if name == "tcp":
            return TCP_CAPABILITIES
        if name == "quic":
            return QUIC_CAPABILITIES
        return None


TCP_CAPABILITIES  = ProtocolCapabilities(name=ProtocolType.TCP,  version="1.0", port_offset=0)
QUIC_CAPABILITIES = ProtocolCapabilities(name=ProtocolType.QUIC, version="1.0", port_offset=1)


class TransferProtocol(ABC):
    """Abstract base class for file transfer protocols.
    
    All protocol implementations must inherit from this class.
    This allows easy swapping between Python and C++ implementations.
    """
    
    def __init__(self, identity, cfg):
        self.identity = identity
        self.cfg = cfg
        self._capabilities: ProtocolCapabilities = None
    
    @property
    def capabilities(self) -> ProtocolCapabilities:
        """Return protocol capabilities."""
        return self._capabilities
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if this protocol can be used (dependencies available)."""
        pass
    
    @abstractmethod
    def start_server(self, handler_callback) -> bool:
        """Start listening for incoming connections.
        
        Args:
            handler_callback: Function to call for each incoming transfer
                              Signature: (conn_info, files, total_size) -> bool
        
        Returns:
            True if server started successfully
        """
        pass
    
    @abstractmethod
    def stop_server(self):
        """Stop the server."""
        pass
    
    @abstractmethod
    def send_files(
        self,
        host: str,
        port: int,
        files: List[str],
        progress_callback=None,
        total_size: int = 0,
    ) -> bool:
        """Send files to a remote peer.

        Args:
            host: Target host address
            port: Target port
            files: List of file paths to send
            progress_callback: Called with (bytes_sent, total_bytes)
            total_size: Pre-computed sum of file sizes in bytes.
                        If 0, the implementation computes it internally.

        Returns:
            True if all files sent successfully
        """
    
    def receive_files(self, conn_info, download_dir: str, progress_callback=None) -> bool:
        """Receive files from an incoming connection.

        Default no-op: implementations that handle receiving inline
        (e.g. TCPProtocol._handle_connection) do not need to override this.
        """
        return True


class ProtocolSelector:
    """Selects the best available protocol for a transfer."""
    
    def __init__(self, identity, cfg):
        self.identity = identity
        self.cfg = cfg
        self._protocols: List[TransferProtocol] = []
        self._discover_protocols()
    
    def _discover_protocols(self):
        """Discover and initialize available protocols."""
        # Import protocol implementations from consolidated module
        from transfer import QUICProtocol, TCPProtocol
        
        # Try QUIC first (faster), fallback to TCP
        protocols_to_try = [
            (QUICProtocol, "QUIC"),
            (TCPProtocol, "TCP"),
        ]
        
        for protocol_class, name in protocols_to_try:
            try:
                proto = protocol_class(self.identity, self.cfg)
                if proto.is_available():
                    self._protocols.append(proto)
                    LOG.info(f"Protocol {name} available")
                else:
                    LOG.debug(f"Protocol {name} not available")
            except Exception as e:
                LOG.warning(f"Failed to initialize {name}: {e}")
    
    def get_protocols(self) -> List[TransferProtocol]:
        """Get list of available protocols, ordered by preference."""
        return list(self._protocols)
    
    def get_capabilities(self) -> List[ProtocolCapabilities]:
        """Get capabilities for advertisement."""
        return [p.capabilities for p in self._protocols]
    
    def select_for_peer(
        self,
        peer_capabilities: List[ProtocolCapabilities],
    ) -> Optional[TransferProtocol]:
        """Select the best protocol supported by both peers.

        Iterates local protocols in preference order (QUIC first, then TCP)
        and returns the first one the peer also supports.  Falls back to TCP
        when there is no name match.
        """
        if not self._protocols:
            return None

        if not peer_capabilities:
            # Peer sent no capabilities — assume TCP only.
            return self.get_protocol(ProtocolType.TCP) or self._protocols[0]

        peer_names = {c.name for c in peer_capabilities}
        for proto in self._protocols:
            if proto.capabilities.name in peer_names:
                return proto

        # No name match — TCP is always the interoperable fallback.
        return self.get_protocol(ProtocolType.TCP) or self._protocols[0]
    
    def get_protocol(self, proto_type: ProtocolType) -> Optional[TransferProtocol]:
        """Get specific protocol implementation."""
        for proto in self._protocols:
            if proto.capabilities.name == proto_type:
                return proto
        return None

    def remove_protocol(self, protocol: TransferProtocol) -> None:
        """Remove a protocol from the available set (best-effort)."""
        try:
            self._protocols.remove(protocol)
        except ValueError:
            pass
