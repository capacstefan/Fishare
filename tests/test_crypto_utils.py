"""Tests for crypto_utils — self-signed certificate generation."""
from __future__ import annotations

from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from fishare import config, crypto_utils


class TestEnsureCert:
    def setup_method(self):
        # Force regeneration each test.
        for p in (config.CERT_FILE, config.KEY_FILE):
            Path(p).unlink(missing_ok=True)

    def test_creates_files_when_missing(self):
        cert, key = crypto_utils.ensure_cert()
        assert Path(cert).exists()
        assert Path(key).exists()
        assert Path(cert).stat().st_size > 0
        assert Path(key).stat().st_size > 0

    def test_idempotent_when_already_present(self):
        cert1, key1 = crypto_utils.ensure_cert()
        b1 = Path(cert1).read_bytes()
        cert2, key2 = crypto_utils.ensure_cert()
        b2 = Path(cert2).read_bytes()
        assert cert1 == cert2 and key1 == key2
        assert b1 == b2  # not regenerated

    def test_cert_is_valid_x509_with_expected_subject(self):
        cert_path, _ = crypto_utils.ensure_cert()
        cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        cns = [a.value for a in cert.subject]
        assert config.APP_NAME in cns

    def test_key_loads_as_rsa(self):
        _, key_path = crypto_utils.ensure_cert()
        key = serialization.load_pem_private_key(
            Path(key_path).read_bytes(), password=None
        )
        assert key.key_size >= 2048
