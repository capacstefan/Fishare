"""Tests for p2p_lan_share.storage — atomic JSON persistence."""
from __future__ import annotations

import json
import threading

from p2p_lan_share import config, storage


class TestSettings:
    def test_defaults_when_missing(self):
        s = storage.load_settings()
        assert "device_name" in s and s["device_name"]
        assert s["online"] is True
        assert str(config.DEFAULT_DOWNLOAD_DIR) == s["download_dir"]

    def test_save_and_reload_roundtrip(self):
        storage.save_settings({"device_name": "Lab-PC", "online": False,
                               "download_dir": "C:/tmp"})
        s = storage.load_settings()
        assert s["device_name"] == "Lab-PC"
        assert s["online"] is False
        assert s["download_dir"] == "C:/tmp"

    def test_defaults_merge_with_partial_file(self):
        # Only one key written; defaults must fill the rest.
        config.SETTINGS_FILE.write_text(json.dumps({"device_name": "Solo"}),
                                        encoding="utf-8")
        s = storage.load_settings()
        assert s["device_name"] == "Solo"
        assert s["online"] is True   # default

    def test_corrupt_file_falls_back_to_defaults(self):
        config.SETTINGS_FILE.write_text("not json {{{", encoding="utf-8")
        s = storage.load_settings()
        assert s["online"] is True


class TestHistory:
    def test_empty_on_first_load(self):
        assert storage.load_history() == []

    def test_append_and_read(self):
        storage.append_history({"ts": "t1", "msg": "hello"})
        storage.append_history({"ts": "t2", "msg": "world"})
        hist = storage.load_history()
        assert len(hist) == 2
        assert hist[0]["msg"] == "hello"
        assert hist[1]["msg"] == "world"

    def test_clear(self):
        storage.append_history({"x": 1})
        storage.clear_history()
        assert storage.load_history() == []

    def test_append_is_thread_safe(self):
        # NOTE: Windows can transiently fail os.replace if AV / indexer is
        # scanning the file. Keep load light so the test focuses on the
        # serialisation guarantee, not on stressing the filesystem.
        n_threads, per_thread = 4, 10
        errors: list[BaseException] = []

        def worker(i):
            try:
                for j in range(per_thread):
                    storage.append_history({"t": i, "j": j})
            except BaseException as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert not errors, f"thread errors: {errors}"
        assert len(storage.load_history()) == n_threads * per_thread


class TestQuickTexts:
    def test_default_empty(self):
        assert storage.load_quicktexts() == []

    def test_save_roundtrip(self):
        items = [{"from": "A", "text": "hi", "ts": "t"}]
        storage.save_quicktexts(items)
        assert storage.load_quicktexts() == items


class TestMuted:
    def test_default_empty_set(self):
        assert storage.load_muted() == set()

    def test_save_and_reload(self):
        storage.save_muted({"peerA", "peerB"})
        assert storage.load_muted() == {"peerA", "peerB"}

    def test_saved_format_is_sorted_list(self):
        storage.save_muted({"z", "a", "m"})
        raw = json.loads(config.MUTED_FILE.read_text("utf-8"))
        assert raw == ["a", "m", "z"]


class TestAtomicWrite:
    def test_no_partial_file_left_on_success(self):
        storage.save_settings({"k": "v"})
        tmp = config.SETTINGS_FILE.with_suffix(config.SETTINGS_FILE.suffix + ".tmp")
        assert not tmp.exists()
