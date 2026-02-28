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
    """Describes what a protocol implementation supports."""
    
    name: ProtocolType
    version: str
    supports_multiplexing: bool  # Can send multiple files in parallel
    supports_0rtt: bool          # Can establish connection with 0-RTT
    requires_tls: bool           # Built-in encryption
    port_offset: int             # Port offset from base listen_port
    
    def to_dict(self) -> dict:
        """Serialize for network advertisement."""
        return {
            "name": self.name.value,
            "version": self.version,
            "multiplexing": self.supports_multiplexing,
            "0rtt": self.supports_0rtt,
        }
    
    @staticmethod
    def from_dict(data: dict) -> Optional["ProtocolCapabilities"]:
        """Deserialize from network advertisement."""
        try:
            name = data.get("name", "tcp")
            if name == "tcp":
                return TCP_CAPABILITIES
            elif name == "quic":
                return QUIC_CAPABILITIES
        except Exception:
            return None


# Protocol capability constants
TCP_CAPABILITIES = ProtocolCapabilities(
    name=ProtocolType.TCP,
    version="1.0",
    supports_multiplexing=False,
    supports_0rtt=False,
    requires_tls=False,
    port_offset=0,
)

QUIC_CAPABILITIES = ProtocolCapabilities(
    name=ProtocolType.QUIC,
    version="1.0",
    supports_multiplexing=True,
    supports_0rtt=True,
    requires_tls=True,
    port_offset=1,  # QUIC uses listen_port + 1
)


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
        progress_callback=None
    ) -> bool:
        """Send files to a remote peer.
        
        Args:
            host: Target host address
            port: Target port
            files: List of file paths to send
            progress_callback: Called with (bytes_sent, total_bytes)
        
        Returns:
            True if all files sent successfully
        """
        pass
    
    @abstractmethod
    def receive_files(
        self,
        conn_info,
        download_dir: str,
        progress_callback=None
    ) -> bool:
        """Receive files from an incoming connection.
        
        Args:
            conn_info: Connection info from server callback
            download_dir: Where to save received files
            progress_callback: Called with (bytes_received, total_bytes)
        
        Returns:
            True if all files received successfully
        """
        pass


class ProtocolSelector:
    """Selects the best available protocol for a transfer."""
    
    def __init__(self, identity, cfg):
        self.identity = identity
        self.cfg = cfg
        self._protocols: List[TransferProtocol] = []
        self._discover_protocols()
    
    def _discover_protocols(self):
        """Discover and initialize available protocols."""
        # Import protocol implementations
        from transfer_quic import QUICProtocol
        from transfer_tcp import TCPProtocol
        
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
        peer_capabilities: List[ProtocolCapabilities]
    ) -> Optional[TransferProtocol]:
        """Select best protocol that both peers support.
        
        Priority:
        1. QUIC (if both support)
        2. TCP (fallback, always available)
        """
        if not self._protocols:
            LOG.error("No protocols available!")
            return None
        
        if not peer_capabilities:
            # Peer didn't advertise capabilities, assume TCP only
            tcp = self.get_protocol(ProtocolType.TCP)
            if tcp:
                return tcp
            # Fallback to first available protocol
            LOG.warning("TCP not available, using first protocol")
            return self._protocols[0] if self._protocols else None
        
        # Try to find best match
        for our_proto in self._protocols:
            for peer_cap in peer_capabilities:
                if our_proto.capabilities.name == peer_cap.name:
                    LOG.info(
                        f"Selected {our_proto.capabilities.name.value} for transfer"
                    )
                    return our_proto
        
        # No match - use TCP fallback if available
        tcp = self.get_protocol(ProtocolType.TCP)
        if tcp:
            LOG.warning("No protocol match, falling back to TCP")
            return tcp
        
        # Last resort: use any available protocol
        if self._protocols:
            LOG.warning(f"Using fallback: {self._protocols[0].capabilities.name.value}")
            return self._protocols[0]
        
        LOG.error("No compatible protocol found!")
        return None
    
    def get_protocol(self, proto_type: ProtocolType) -> Optional[TransferProtocol]:
        """Get specific protocol implementation."""
        for proto in self._protocols:
            if proto.capabilities.name == proto_type:
                return proto
        return None
