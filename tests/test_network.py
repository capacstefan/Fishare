"""End-to-end network tests for TransferServer / TransferTask over TLS.

Uses real loopback sockets and the generated self-signed cert. We patch
``config.TCP_PORT`` per-test to avoid clashing with a running app instance.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from p2p_lan_share import config, network
from p2p_lan_share.network import (
    FileSpec, IncomingOffer, TransferQueue, TransferServer, TransferTask,
)

# Server-side signals fire from receiver threads. Without a Qt event loop,
# AutoConnection becomes QueuedConnection across threads and never delivers,
# so tests connect using DirectConnection explicitly.
_DIRECT = Qt.ConnectionType.DirectConnection


# -------------------------------------------------------------------------
# Pure data-classes
# -------------------------------------------------------------------------
class TestFileSpec:
    def test_name_property_strips_dir(self):
        s = FileSpec(path="C:/tmp/folder/test.bin", size=10)
        assert s.name == "test.bin"


class TestIncomingOffer:
    def test_respond_unblocks_wait(self):
        o = IncomingOffer(kind="text", sender_name="X", sender_id="x")

        def responder():
            time.sleep(0.05)
            o.respond(True, "1234")

        threading.Thread(target=responder, daemon=True).start()
        ok, pin = o.wait(timeout=2.0)
        assert ok is True and pin == "1234"

    def test_wait_times_out_returns_false(self):
        o = IncomingOffer(kind="text", sender_name="X", sender_id="x")
        ok, _ = o.wait(timeout=0.05)
        assert ok is False


# -------------------------------------------------------------------------
# Sender-side cancellation logic
# -------------------------------------------------------------------------
class TestTransferTaskCancel:
    def _make(self):
        return TransferTask(
            peer_name="Peer", peer_addr="127.0.0.1", peer_port=1,
            kind="files", from_name="me", from_id="me",
            files=[], text="", pin="",
        )

    def test_cancel_returns_true_first_time_then_false(self):
        t = self._make()
        assert t.cancel() is True
        assert t.cancel() is False
        assert t.cancelled is True

    def test_run_unknown_kind_emits_failed(self):
        t = TransferTask("Peer", "127.0.0.1", 1, kind="bogus",
                         from_name="me", from_id="me")
        results = []
        t.finished.connect(lambda *a: results.append(a))
        t.run()
        assert results and results[0][1] is False

    def test_pre_cancel_short_circuits(self):
        t = self._make()
        t.cancel()
        results = []
        t.finished.connect(lambda *a: results.append(a))
        t.run()
        assert results and results[0][1] is False
        assert results[0][2] == "cancelled"


# -------------------------------------------------------------------------
# End-to-end TLS file transfer over loopback
# -------------------------------------------------------------------------
@pytest.fixture
def transfer_server(monkeypatch, free_tcp_port, tmp_download_dir):
    monkeypatch.setattr(config, "TCP_PORT", free_tcp_port)
    state = {"online": True, "muted": set(),
             "download_dir": str(tmp_download_dir)}
    srv = TransferServer(lambda: state)
    srv.start()
    yield srv, state, free_tcp_port, tmp_download_dir
    srv.stop()


@pytest.mark.integration
class TestEndToEndTransfer:
    def test_text_transfer(self, transfer_server):
        srv, _state, port, _dl = transfer_server

        # Auto-accept any offer.
        srv.offer_received.connect(lambda o: o.respond(True), _DIRECT)

        got = []
        srv.text_received.connect(
            lambda sender, text: got.append((sender, text)), _DIRECT
        )

        task = TransferTask(
            peer_name="loop", peer_addr="127.0.0.1", peer_port=port,
            kind="text", from_name="Tester", from_id="abc123",
            text="hello from test",
        )
        done = threading.Event()
        task.finished.connect(lambda *_: done.set())
        task.run()

        assert done.wait(5.0), "transfer did not finish"
        # Allow signal to propagate.
        for _ in range(50):
            if got: break
            time.sleep(0.05)
        assert got and got[0][1] == "hello from test"

    def test_file_transfer_integrity(self, transfer_server, tmp_path):
        srv, _state, port, dl = transfer_server
        srv.offer_received.connect(lambda o: o.respond(True), _DIRECT)

        src = tmp_path / "payload.bin"
        payload = bytes(range(256)) * 4096  # ~1 MB, varied bytes
        src.write_bytes(payload)

        completed = threading.Event()
        names_received: list = []
        srv.transfer_completed.connect(
            lambda sender, names, total: (names_received.extend(names), completed.set()),
            _DIRECT,
        )

        task = TransferTask(
            peer_name="loop", peer_addr="127.0.0.1", peer_port=port,
            kind="files", from_name="Tester", from_id="abc",
            files=[FileSpec(path=str(src), size=src.stat().st_size)],
        )
        done = threading.Event()
        task.finished.connect(lambda *_: done.set())
        task.run()

        assert done.wait(10.0)
        assert completed.wait(2.0)
        assert names_received, "no file recorded as received"
        saved = dl / names_received[0]
        assert saved.exists()
        assert saved.read_bytes() == payload

    def test_offline_state_rejects_offer(self, transfer_server):
        srv, state, port, _dl = transfer_server
        state["online"] = False

        task = TransferTask("loop", "127.0.0.1", port, "text",
                            from_name="X", from_id="x", text="hi")
        finished = []
        task.finished.connect(lambda *a: finished.append(a))
        task.run()
        assert finished and finished[0][1] is False
        assert "offline" in finished[0][2].lower()

    def test_muted_sender_rejected(self, transfer_server):
        srv, state, port, _dl = transfer_server
        state["muted"].add("blocked-id")

        task = TransferTask("loop", "127.0.0.1", port, "text",
                            from_name="Spam", from_id="blocked-id", text="hi")
        finished = []
        task.finished.connect(lambda *a: finished.append(a))
        task.run()
        assert finished and finished[0][1] is False
        assert "mute" in finished[0][2].lower()

    def test_recipient_rejection(self, transfer_server):
        srv, _state, port, _dl = transfer_server
        srv.offer_received.connect(lambda o: o.respond(False), _DIRECT)

        task = TransferTask("loop", "127.0.0.1", port, "text",
                            from_name="X", from_id="x", text="hi")
        finished = []
        task.finished.connect(lambda *a: finished.append(a))
        task.run()
        assert finished and finished[0][1] is False


# -------------------------------------------------------------------------
# Queue concurrency cap
# -------------------------------------------------------------------------
class TestTransferQueue:
    def test_runs_submitted_task(self):
        q = TransferQueue(max_concurrent=2)
        t = TransferTask("Peer", "127.0.0.1", 1, kind="bogus",
                         from_name="me", from_id="me")
        done = threading.Event()
        t.finished.connect(lambda *_: done.set(), _DIRECT)
        q.submit(t)
        assert done.wait(2.0)

    def test_cancelled_before_submit_does_not_run(self):
        q = TransferQueue(max_concurrent=1)
        t = TransferTask("Peer", "127.0.0.1", 1, kind="files",
                         from_name="me", from_id="me",
                         files=[FileSpec("nonexistent.bin", 1)])
        t.cancel()
        finished = []
        t.finished.connect(lambda *a: finished.append(a), _DIRECT)
        q.submit(t)
        time.sleep(0.3)
        assert finished and finished[0][2] == "cancelled"
