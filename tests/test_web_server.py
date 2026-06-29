"""Tests for QrWebServer — uses Flask's test client (no real socket bind)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fishare import config
from fishare.web_server import QrWebServer


@pytest.fixture
def qrws(tmp_path):
    s = QrWebServer(device_name="UnitDev", download_dir=str(tmp_path))
    app = s._make_app()
    app.testing = True
    return s, app.test_client(), tmp_path


class TestRouting:
    def test_index_requires_valid_token(self, qrws):
        srv, c, _ = qrws
        assert c.get("/wrong-token/").status_code == 404

    def test_index_with_valid_token_renders(self, qrws):
        srv, c, _ = qrws
        r = c.get(f"/{srv._token}/")
        assert r.status_code == 200
        assert b"Send to" in r.data
        assert b"UnitDev" in r.data


class TestUpload:
    def test_upload_saves_file_and_emits_signal(self, qrws):
        srv, c, dest = qrws
        events = []
        srv.file_received.connect(lambda name, path, size: events.append((name, path, size)))

        r = c.post(
            f"/{srv._token}/upload",
            data={"files": [(_make_stream(b"hello"), "hello.txt")]},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        assert b"Uploaded" in r.data
        saved = list(dest.iterdir())
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"hello"
        assert events and events[0][0] == "hello.txt"

    def test_upload_rejects_no_files(self, qrws):
        srv, c, _ = qrws
        r = c.post(
            f"/{srv._token}/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        assert b"No files" in r.data

    def test_too_many_files(self, qrws, monkeypatch):
        srv, c, _ = qrws
        # Pull current limit; build N+1 entries.
        n = config.WEB_MAX_FILES
        files = [(_make_stream(b"x"), f"f{i}.txt") for i in range(n + 1)]
        r = c.post(
            f"/{srv._token}/upload",
            data={"files": files},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        assert b"Too many" in r.data

    def test_upload_unique_filenames(self, qrws):
        srv, c, dest = qrws
        (dest / "same.txt").write_bytes(b"existing")
        r = c.post(
            f"/{srv._token}/upload",
            data={"files": [(_make_stream(b"new"), "same.txt")]},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        names = sorted(p.name for p in dest.iterdir())
        assert "same.txt" in names
        assert any("(1)" in n for n in names)


class TestTextEndpoint:
    def test_send_text_ok(self, qrws):
        srv, c, _ = qrws
        seen = []
        srv.text_received.connect(lambda sender, text: seen.append((sender, text)))
        r = c.post(f"/{srv._token}/text", data={"text": "hello world"})
        assert r.status_code == 200
        assert seen == [("Phone", "hello world")]

    def test_send_text_empty_rejected(self, qrws):
        srv, c, _ = qrws
        r = c.post(f"/{srv._token}/text", data={"text": "   "})
        assert b"Empty text" in r.data

    def test_text_truncated_to_max_chars(self, qrws):
        srv, c, _ = qrws
        seen = []
        srv.text_received.connect(lambda sender, text: seen.append((sender, text)))
        long_text = "x" * (config.QUICK_TEXT_MAX_CHARS + 200)
        c.post(f"/{srv._token}/text", data={"text": long_text})
        assert seen
        assert len(seen[0][1]) == config.QUICK_TEXT_MAX_CHARS

    def test_text_requires_valid_token(self, qrws):
        srv, c, _ = qrws
        r = c.post("/bad/text", data={"text": "hi"})
        assert r.status_code == 404


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------
def _make_stream(data: bytes):
    import io
    return io.BytesIO(data)
