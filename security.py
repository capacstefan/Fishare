"""Cryptographic identity and AEAD stream for secure transfers."""

import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import DATA_DIR, KEY_FILE


class AEADStream:
    """ChaCha20-Poly1305 AEAD stream with incremental nonce.

    The raw key bytes and nonce counters are exposed as properties so that the
    C++ engine (cpp_engine) can be initialised from the same session state after
    the JSON handshake phase completes.  The C++ engine increments its own copy
    of the nonce and returns the new value; callers must write it back here
    (aead.send_nonce = new_val) to keep both sides in sync.
    """

    def __init__(self, key: bytes):
        self._key  = bytes(key)          # retained for C++ engine initialisation
        self._aead = ChaCha20Poly1305(key)
        self._send_nonce = 0
        self._recv_nonce = 0

    # ── Properties for C++ engine integration ──────────

    @property
    def key(self) -> bytes:
        """Raw 32-byte session key."""
        return self._key

    @property
    def send_nonce(self) -> int:
        """Current send-side nonce counter."""
        return self._send_nonce

    @send_nonce.setter
    def send_nonce(self, value: int) -> None:
        """Sync send-nonce after C++ engine finishes a batch of frames."""
        self._send_nonce = int(value)

    @property
    def recv_nonce(self) -> int:
        """Current receive-side nonce counter."""
        return self._recv_nonce

    @recv_nonce.setter
    def recv_nonce(self, value: int) -> None:
        """Sync recv-nonce after C++ engine finishes a batch of frames."""
        self._recv_nonce = int(value)

    # ── AEAD primitives ────────────────────────────────

    def _n2b(self, n: int) -> bytes:
        return n.to_bytes(12, "big")

    def encrypt(self, data: bytes) -> bytes:
        nonce = self._n2b(self._send_nonce)
        self._send_nonce += 1
        return self._aead.encrypt(nonce, data, b"FIshare")

    def decrypt(self, data: bytes) -> bytes:
        nonce = self._n2b(self._recv_nonce)
        self._recv_nonce += 1
        return self._aead.decrypt(nonce, data, b"FIshare")


def _recv_exact(sock, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*, or raise ConnectionError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed connection during handshake")
        buf.extend(chunk)
    return bytes(buf)


def key_agree(sock, sign_func, my_identity_pub: bytes) -> tuple:
    """Ephemeral ECDH key-agreement with mutual identity key exchange.

    Wire format (both sides send in this order):
      [2-byte X25519 len] [32-byte X25519 pub]
      [2-byte identity len] [32-byte Ed25519 identity pub]
      [2-byte sig len] [64-byte Ed25519 signature of X25519 pub]

    The signature is ALWAYS verified against the received identity key,
    guaranteeing the peer controls the private key matching the identity pub
    they claim.  The caller is responsible for TOFU-checking the returned
    identity key against its own trust store.

    Returns:
        (AEADStream, peer_identity_pub_bytes)
    """
    X25519_KEY_LEN = 32
    ED25519_PUB_LEN = 32
    ED25519_SIG_LEN = 64

    my_priv = X25519PrivateKey.generate()
    my_pub_bytes = my_priv.public_key().public_bytes_raw()

    sig = sign_func(my_pub_bytes)

    # Send our X25519 pub, Ed25519 identity pub, and signature
    sock.sendall(len(my_pub_bytes).to_bytes(2, "big") + my_pub_bytes)
    sock.sendall(len(my_identity_pub).to_bytes(2, "big") + my_identity_pub)
    sock.sendall(len(sig).to_bytes(2, "big") + sig)

    # Receive peer X25519 pub
    plen = int.from_bytes(_recv_exact(sock, 2), "big")
    if plen != X25519_KEY_LEN:
        raise ValueError(f"Bad X25519 key length from peer: {plen}")
    peer_pub_bytes = _recv_exact(sock, plen)

    # Receive peer Ed25519 identity pub
    ilen = int.from_bytes(_recv_exact(sock, 2), "big")
    if ilen != ED25519_PUB_LEN:
        raise ValueError(f"Bad identity key length from peer: {ilen}")
    peer_identity_pub = _recv_exact(sock, ilen)

    # Receive peer signature
    slen = int.from_bytes(_recv_exact(sock, 2), "big")
    if slen != ED25519_SIG_LEN:
        raise ValueError(f"Bad signature length from peer: {slen}")
    peer_sig = _recv_exact(sock, slen)

    # Always verify: the peer must own the private key matching the identity
    # key they sent.  This proves possession; TOFU checks history separately.
    ed25519.Ed25519PublicKey.from_public_bytes(peer_identity_pub).verify(
        peer_sig, peer_pub_bytes
    )

    peer_key = X25519PublicKey.from_public_bytes(peer_pub_bytes)
    shared = my_priv.exchange(peer_key)

    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"FIshare-key-v1",
    ).derive(shared)

    return AEADStream(key), bytes(peer_identity_pub)


class Identity:
    """Persistent Ed25519 signing identity."""

    def __init__(self):
        self._priv = None
        self._pub = None

    def load_or_create(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                self._priv = serialization.load_pem_private_key(f.read(), password=None)
        else:
            self._priv = ed25519.Ed25519PrivateKey.generate()
            pem = self._priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            with open(KEY_FILE, "wb") as f:
                f.write(pem)
        self._pub = self._priv.public_key()

    def sign(self, data: bytes) -> bytes:
        return self._priv.sign(data)

    def public_bytes(self) -> bytes:
        return self._pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
