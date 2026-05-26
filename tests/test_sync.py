"""Tests for sync.SyncSender / SyncReceiver.

We bypass the network for unit tests by handing each side one end of a
``socket.socketpair`` wrapped in a Wire (no TLS). Path-traversal sanitation
in the receiver is tested directly.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from p2p_lan_share import sync
from p2p_lan_share.protocol import Wire
from p2p_lan_share.sync import SyncReceiver, SyncSender


def _wire_pair() -> tuple[Wire, Wire]:
    a, b = socket.socketpair()
    return Wire(a), Wire(b)


# -------------------------------------------------------------------------
# Path traversal protection in receiver
# -------------------------------------------------------------------------
class TestSafePath:
    def test_normal_path_resolves_inside_dest(self, tmp_path):
        a, b = _wire_pair()
        try:
            r = SyncReceiver(b, str(tmp_path))
            out = r._safe("sub/file.txt")
            assert out is not None
            assert tmp_path.resolve() in out.parents or out.parent == tmp_path.resolve()
        finally:
            a.close(); b.close()

    def test_parent_escape_returns_none(self, tmp_path):
        a, b = _wire_pair()
        try:
            r = SyncReceiver(b, str(tmp_path))
            assert r._safe("../evil.txt") is None
            assert r._safe("../../etc/passwd") is None
        finally:
            a.close(); b.close()

    def test_absolute_paths_are_neutralised(self, tmp_path):
        a, b = _wire_pair()
        try:
            r = SyncReceiver(b, str(tmp_path))
            # Leading slashes are stripped, so an "absolute" rel becomes relative.
            out = r._safe("/foo/bar.txt")
            assert out is not None
            assert "bar.txt" in str(out)
        finally:
            a.close(); b.close()

    def test_backslash_paths_normalised(self, tmp_path):
        a, b = _wire_pair()
        try:
            r = SyncReceiver(b, str(tmp_path))
            out = r._safe("sub\\nested\\f.txt")
            assert out is not None
            assert "f.txt" in str(out)
        finally:
            a.close(); b.close()


# -------------------------------------------------------------------------
# End-to-end put / delete over a socketpair
# -------------------------------------------------------------------------
class TestPutAndDeleteRoundtrip:
    def test_sender_initial_scan_transfers_files(self, tmp_path):
        src = tmp_path / "src"; src.mkdir()
        dst = tmp_path / "dst"; dst.mkdir()
        (src / "a.txt").write_bytes(b"hello A")
        (src / "sub").mkdir()
        (src / "sub" / "b.bin").write_bytes(b"\x00\x01\x02" * 1000)

        a, b = _wire_pair()
        sender = SyncSender(a, str(src))
        receiver = SyncReceiver(b, str(dst))

        receiver.start()
        sender.start()

        # Wait for both files to arrive.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if (dst / "a.txt").exists() and (dst / "sub" / "b.bin").exists():
                break
            time.sleep(0.05)

        try:
            assert (dst / "a.txt").read_bytes() == b"hello A"
            assert (dst / "sub" / "b.bin").read_bytes() == b"\x00\x01\x02" * 1000
        finally:
            sender.stop(notify=False)
            receiver.stop(notify=False)

    def test_delete_event_removes_file(self, tmp_path):
        dst = tmp_path / "dst"; dst.mkdir()
        victim = dst / "gone.txt"
        victim.write_bytes(b"bye")

        a, b = _wire_pair()
        receiver = SyncReceiver(b, str(dst))
        receiver.start()
        try:
            a.send_json({"type": "sync_event", "op": "delete", "path": "gone.txt"})
            deadline = time.monotonic() + 2.0
            while victim.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not victim.exists()
        finally:
            a.send_json({"type": "sync_event", "op": "stop"})
            receiver.stop(notify=False)


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------
class TestRelHelper:
    def test_rel_uses_forward_slashes(self, tmp_path):
        (tmp_path / "x").mkdir()
        f = tmp_path / "x" / "y.txt"
        f.write_text("z")
        assert sync._rel(tmp_path, f) == "x/y.txt"
