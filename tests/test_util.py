"""Tests for fishare.util — pure helpers, no I/O dependencies."""
from __future__ import annotations

from pathlib import Path

from fishare.util import fmt_size, fmt_eta, unique_path, local_ip


class TestFmtSize:
    def test_bytes(self):
        assert fmt_size(0) == "0 B"
        assert fmt_size(512) == "512 B"

    def test_kilobytes(self):
        assert fmt_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert fmt_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert fmt_size(3 * 1024 ** 3) == "3.0 GB"

    def test_terabytes(self):
        assert fmt_size(2 * 1024 ** 4) == "2.0 TB"


class TestFmtEta:
    def test_zero_or_negative_bps(self):
        assert fmt_eta(1000, 0) == "--"
        assert fmt_eta(1000, -1) == "--"

    def test_seconds(self):
        assert fmt_eta(100, 10) == "10s"

    def test_minutes(self):
        # 130 / 1 = 130 s => 2m10s
        assert fmt_eta(130, 1) == "2m10s"

    def test_hours(self):
        # 3661 / 1 = 1h01m
        assert fmt_eta(3661, 1) == "1h01m"


class TestUniquePath:
    def test_returns_same_path_if_missing(self, tmp_path: Path):
        p = tmp_path / "file.txt"
        assert unique_path(p) == p

    def test_appends_counter_when_exists(self, tmp_path: Path):
        p = tmp_path / "file.txt"
        p.write_text("x")
        out = unique_path(p)
        assert out.name == "file (1).txt"
        assert out.parent == tmp_path

    def test_increments_until_free(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "a (1).txt").write_text("x")
        (tmp_path / "a (2).txt").write_text("x")
        out = unique_path(tmp_path / "a.txt")
        assert out.name == "a (3).txt"

    def test_handles_no_extension(self, tmp_path: Path):
        (tmp_path / "README").write_text("x")
        out = unique_path(tmp_path / "README")
        assert out.name == "README (1)"


class TestLocalIp:
    def test_returns_ipv4_string(self):
        ip = local_ip()
        # Either a real LAN IP or the documented fallback.
        parts = ip.split(".")
        assert len(parts) == 4
        assert all(0 <= int(p) <= 255 for p in parts)
