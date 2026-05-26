"""Tests for discovery.PeerRegistry — logic only, no real mDNS network."""
from __future__ import annotations

from p2p_lan_share.discovery import Peer, PeerRegistry, _peer_id


class TestPeer:
    def test_display_online(self):
        p = Peer("id1", "Alice", "192.168.1.5", 51821, "online", False)
        assert "Alice" in p.display
        assert "🟢" in p.display

    def test_display_offline_and_muted(self):
        p = Peer("id2", "Bob", "192.168.1.6", 51821, "offline", True)
        assert "🔴" in p.display
        assert "🔇" in p.display


class TestPeerIdStability:
    def test_peer_id_is_deterministic_for_same_cert(self):
        a = _peer_id()
        b = _peer_id()
        assert a == b
        # 16 hex chars => 8 bytes prefix.
        assert len(a) == 16
        int(a, 16)  # parses as hex


class TestPeerRegistryMute:
    def test_default_state(self):
        reg = PeerRegistry("dev", True)
        assert reg.muted == set()
        assert reg.online is True

    def test_initial_muted_set(self):
        reg = PeerRegistry("dev", True, muted={"pid-x"})
        assert reg.is_muted("pid-x") is True

    def test_toggle_mute_returns_new_state(self):
        reg = PeerRegistry("dev", True)
        assert reg.toggle_mute("p1") is True
        assert reg.is_muted("p1") is True
        assert reg.toggle_mute("p1") is False
        assert reg.is_muted("p1") is False

    def test_toggle_mute_updates_existing_peer(self):
        reg = PeerRegistry("dev", True)
        peer = Peer("p1", "X", "1.2.3.4", 1, "online", False)
        reg.peers["p1"] = peer
        seen = []
        reg.peer_updated.connect(lambda p: seen.append(p))
        reg.toggle_mute("p1")
        assert peer.muted is True
        assert seen and seen[0] is peer

    def test_set_device_name_falls_back_when_empty(self):
        reg = PeerRegistry("dev", True)
        reg.set_device_name("")
        assert reg.device_name  # non-empty default

    def test_find_by_name(self):
        reg = PeerRegistry("dev", True)
        reg.peers["p1"] = Peer("p1", "Alice", "1.1.1.1", 1)
        reg.peers["p2"] = Peer("p2", "Bob", "1.1.1.2", 1)
        assert reg.find_by_name("Bob").peer_id == "p2"
        assert reg.find_by_name("Nobody") is None
