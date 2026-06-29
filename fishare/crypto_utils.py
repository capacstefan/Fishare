"""Generate a persistent self-signed TLS certificate on first run."""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import config


def ensure_cert() -> tuple[str, str]:
    """Return (cert_path, key_path). Generates them once if missing."""
    if config.CERT_FILE.exists() and config.KEY_FILE.exists():
        return str(config.CERT_FILE), str(config.KEY_FILE)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = socket.gethostname()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, config.APP_NAME),
    ])
    san = [x509.DNSName(hostname), x509.DNSName("localhost")]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    config.CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    config.KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return str(config.CERT_FILE), str(config.KEY_FILE)


def fingerprint_from_der(der_bytes: bytes) -> str:
    return hashlib.sha256(der_bytes).hexdigest()


def fingerprint_from_pem_bytes(pem_bytes: bytes) -> str:
    try:
        pem_text = pem_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pem_text = pem_bytes.decode("utf-8", "ignore")
    der = ssl.PEM_cert_to_DER_cert(pem_text)
    return fingerprint_from_der(der)


def local_cert_fingerprint() -> str:
    cert, _ = ensure_cert()
    return fingerprint_from_pem_bytes(Path(cert).read_bytes())
