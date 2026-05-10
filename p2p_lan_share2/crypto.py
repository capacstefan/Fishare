"""Self-signed TLS certificate creation."""
from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import config


def ensure_cert() -> tuple[str, str]:
    """Create cert/key if missing. Returns (cert_path, key_path)."""
    if config.CERT_FILE.exists() and config.KEY_FILE.exists():
        return str(config.CERT_FILE), str(config.KEY_FILE)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = socket.gethostname()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "P2P LAN Share"),
    ])

    san = [x509.DNSName(hostname), x509.DNSName("localhost")]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    config.CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    config.KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(config.CERT_FILE), str(config.KEY_FILE)
