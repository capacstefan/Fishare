import os
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

from config import KEY_FILE, DATA_DIR


class AEADStream:
    """Simple AEAD stream (ChaCha20-Poly1305) with incremental nonce."""

    def __init__(self, key: bytes):
        self._aead = ChaCha20Poly1305(key)
        self._send_nonce = 0
        self._recv_nonce = 0

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


def key_agree(sock, sign_func, peer_pub=None) -> AEADStream:
    """Performs ephemeral ECDH with signed public key exchange.

    If peer_pub is provided, verify the peer's signature against that public key
    (basic pinning). Otherwise, signatures are exchanged but not verified.
    """
    my_priv = X25519PrivateKey.generate()
    my_pub_bytes = my_priv.public_key().public_bytes_raw()

    # sign our ephemeral key
    sig = sign_func(my_pub_bytes)
    sock.sendall(len(my_pub_bytes).to_bytes(2, "big") + my_pub_bytes)
    sock.sendall(len(sig).to_bytes(2, "big") + sig)

    # receive peer ephemeral pub + sig
    plen = int.from_bytes(sock.recv(2), "big")
    peer_pub_bytes = sock.recv(plen)
    slen = int.from_bytes(sock.recv(2), "big")
    peer_sig = sock.recv(slen)

    # verify signature if peer_pub provided (pinning)
    if peer_pub:
        ed25519.Ed25519PublicKey.from_public_bytes(peer_pub).verify(
            peer_sig, peer_pub_bytes
        )

    peer_key = X25519PublicKey.from_public_bytes(peer_pub_bytes)
    shared = my_priv.exchange(peer_key)

    # derive session key
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"FIshare-key-v1",
    ).derive(shared)

    return AEADStream(key)


class Identity:
    """Persistent Ed25519 identity for signing ephemeral keys."""

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

