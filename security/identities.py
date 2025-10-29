import os
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from settings.storage import CFG_DIR

KEY_PATH = os.path.join(CFG_DIR, "id_ed25519.pem")


class Identity:
    """Persistent Ed25519 identity for signing ephemeral keys."""

    def __init__(self):
        self._priv = None
        self._pub = None

    def load_or_create(self):
        os.makedirs(CFG_DIR, exist_ok=True)
        if os.path.exists(KEY_PATH):
            with open(KEY_PATH, "rb") as f:
                self._priv = serialization.load_pem_private_key(f.read(), password=None)
        else:
            self._priv = ed25519.Ed25519PrivateKey.generate()
            pem = self._priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            with open(KEY_PATH, "wb") as f:
                f.write(pem)
        self._pub = self._priv.public_key()

    def sign(self, data: bytes) -> bytes:
        return self._priv.sign(data)

    def public_bytes(self) -> bytes:
        return self._pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
