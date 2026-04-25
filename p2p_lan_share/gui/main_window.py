"""Main application window: wires tabs, discovery, network, sync, web server."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PyQt6.QtCore import Qt, QPropertyAnimation, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QFileDialog,
    QGraphicsOpacityEffect,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, storage
from ..discovery import PeerRegistry
from ..network import FileSpec, TransferQueue, TransferServer, TransferTask, connect_and_offer
from ..sync import SyncReceiver, SyncSender
from ..web_server import QrWebServer
from .dialogs import AcceptOfferDialog
from .tab_history import HistoryTab
from .tab_quicktext import QuickTextTab
from .tab_tools import ToolsTab
from .tab_transfer import TransferTab


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_fingerprint(s: str) -> bool:
    return len(s) == 16 and all(c in "0123456789abcdef" for c in s)


class NotificationBar(QLabel):
    """Bottom notification with simple fade in/out."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background-color: rgba(29, 29, 31, 0.92);"
            "color: white;"
            "padding: 4px 12px;"
            "border-radius: 9px;"
            "font-size: 10pt;"
            "font-weight: 500;"
        )
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self.setFixedHeight(26)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self._fade(0.0, 500))

    def show_message(self, text: str, duration_ms: int = 2500) -> None:
        self.setText(text)
        self._fade(1.0, 300)
        self._hide_timer.start(duration_ms)

    def _fade(self, target: float, duration: int) -> None:
        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(target)
        self._anim.start()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = storage.load_settings()
        # Keep only fingerprint-style mutes (drops legacy name-based entries
        # from older versions silently).
        self._muted: set[str] = {m for m in storage.load_muted() if _is_fingerprint(m)}
        if self._muted != storage.load_muted():
            storage.save_muted(self._muted)

        self.setWindowTitle(config.APP_NAME)
        self.resize(1200, 780)
        self.setMinimumSize(980, 640)

        self.registry = PeerRegistry(
            self.settings["device_name"], bool(self.settings["online"]), self._muted
        )
        self.server = TransferServer(self._get_server_state)
        self.queue = TransferQueue()
        self.qr_server: QrWebServer | None = None
        self.sync_sender: SyncSender | None = None
        self.sync_receiver: SyncReceiver | None = None
        self._live_tasks: list = []

        # ---- layout ----
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tab_transfer = TransferTab(self.settings)
        self.tab_text = QuickTextTab()
        self.tab_tools = ToolsTab()
        self.tab_history = HistoryTab(clear_cb=storage.clear_history)
        for w, label in [
            (self.tab_transfer, "File Transfer"),
            (self.tab_text, "Quick Text"),
            (self.tab_tools, "Tools"),
            (self.tab_history, "History"),
        ]:
            self.tabs.addTab(w, label)
        layout.addWidget(self.tabs, 1)

        self.notification = NotificationBar(self)
        layout.addWidget(self.notification)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))
        self._refresh_status()

        self.tab_history.load(storage.load_history())
        self.tab_text.load_inbox(storage.load_quicktexts())

        self._connect_signals()
        self.registry.start()
        self.server.start()

    # ---------- signal wiring ----------
    def _connect_signals(self) -> None:
        r, s, tt, txt, tools = (
            self.registry, self.server,
            self.tab_transfer, self.tab_text, self.tab_tools,
        )

        r.peer_added.connect(self._on_peer_added)
        r.peer_updated.connect(self._on_peer_updated)
        r.peer_removed.connect(self._on_peer_removed)

        tt.device_name_changed.connect(self._on_device_name_changed)
        tt.online_toggled.connect(self._on_online_toggled)
        tt.choose_download_dir.connect(self._pick_download_dir)
        tt.send_requested.connect(self._on_send_files)
        tt.mute_toggled.connect(self._on_toggle_mute)

        txt.send_text_requested.connect(self._on_send_text)
        txt.mute_toggled.connect(self._on_toggle_mute)
        txt.inbox_changed.connect(storage.save_quicktexts)

        tools.sync_start_requested.connect(self._on_sync_start)
        tools.sync_stop_requested.connect(self._on_sync_stop)
        tools.qr_start_requested.connect(self._on_qr_start)
        tools.qr_stop_requested.connect(self._on_qr_stop)

        s.offer_received.connect(self._on_offer_received)
        s.file_progress.connect(self._on_recv_file_progress)
        s.transfer_completed.connect(self._on_recv_completed)
        s.recv_failed.connect(tt.on_recv_failed)
        s.text_received.connect(self._on_text_received)
        s.sync_started.connect(self._on_incoming_sync)
        s.log.connect(lambda m: self.statusBar().showMessage(m, 4000))

    # ---------- state ----------
    def _refresh_status(self) -> None:
        s = self.settings
        self.statusBar().showMessage(
            f"Device: {s['device_name']}  |  "
            f"{'Online' if s['online'] else 'Offline'}  |  "
            f"Downloads: {s['download_dir']}"
        )

    def _get_server_state(self) -> dict:
        return {
            "online": bool(self.settings["online"]),
            "muted": set(self._muted),
            "download_dir": self.settings["download_dir"],
        }

    def notify(self, text: str) -> None:
        self.notification.show_message(text)

    # ---------- peer events ----------
    @pyqtSlot(object)
    def _on_peer_added(self, peer) -> None:
        for tab in (self.tab_transfer, self.tab_text, self.tab_tools):
            tab.upsert_peer(peer)

    @pyqtSlot(object)
    def _on_peer_updated(self, peer) -> None:
        for tab in (self.tab_transfer, self.tab_text, self.tab_tools):
            tab.upsert_peer(peer)
        if peer.status == "offline":
            self.tab_transfer.remove_offline_selected(peer.peer_id)

    @pyqtSlot(str)
    def _on_peer_removed(self, peer_id: str) -> None:
        for tab in (self.tab_transfer, self.tab_text, self.tab_tools):
            tab.remove_peer(peer_id)

    # ---------- settings handlers ----------
    def _on_device_name_changed(self, name: str) -> None:
        name = name or config.default_device_name()
        self.settings["device_name"] = name
        storage.save_settings(self.settings)
        self._refresh_status()
        self.registry.set_device_name(name)

    def _on_online_toggled(self, online: bool) -> None:
        # UI updates instantly; mDNS re-announce runs on a background thread.
        self.settings["online"] = bool(online)
        storage.save_settings(self.settings)
        self._refresh_status()
        self.notify("Online" if online else "Offline")
        self.registry.set_online(bool(online))

    def _pick_download_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Default Download Folder", self.settings["download_dir"]
        )
        if folder:
            self.settings["download_dir"] = folder
            storage.save_settings(self.settings)
            self._refresh_status()

    def _on_toggle_mute(self, peer_id: str) -> None:
        peer = self.registry.peers.get(peer_id)
        display = peer.name if peer else peer_id
        now_muted = self.registry.toggle_mute(peer_id)
        self._muted = self.registry.muted
        storage.save_muted(self._muted)
        self.notify(f"{'Muted' if now_muted else 'Unmuted'} {display}")

    # ---------- task helpers ----------
    def _submit_task(self, task: TransferTask) -> None:
        self._live_tasks.append(task)
        task.finished.connect(
            lambda *a, t=task: (self._live_tasks.remove(t) if t in self._live_tasks else None)
        )
        self.queue.submit(task)

    def _new_task(self, peer, kind: str, **kwargs) -> TransferTask:
        return TransferTask(
            peer_name=peer.name,
            peer_addr=peer.address,
            peer_port=peer.port,
            kind=kind,
            from_name=self.settings["device_name"],
            from_id=self.registry.peer_id,
            **kwargs,
        )

    # ---------- send files ----------
    def _on_send_files(self, peer_names: list, file_paths: list, pin: str) -> None:
        specs: list[FileSpec] = []
        for p in file_paths:
            pth = Path(p)
            if not (pth.exists() and pth.is_file()):
                continue
            size = pth.stat().st_size
            if size > config.MAX_FILE_SIZE:
                QMessageBox.warning(
                    self, "File too large",
                    f"{pth.name} exceeds the {config.MAX_FILE_SIZE // (1024**3)} GB limit and will be skipped.",
                )
                continue
            specs.append(FileSpec(path=str(pth), size=size))
        if not specs:
            return

        total = sum(s.size for s in specs)
        sent = 0
        for name in peer_names:
            peer = self.registry.find_by_name(name)
            if peer is None:
                self.tab_transfer.on_task_status(name, "offline")
                continue
            if peer.status != "online":
                self.tab_transfer.on_task_status(name, "offline")
                continue
            task = self._new_task(peer, "files", files=specs, pin=pin)
            task.progress.connect(self.tab_transfer.on_task_progress)
            task.status.connect(self.tab_transfer.on_task_status)
            task.finished.connect(
                lambda peer_name, ok, reason, tot=total, cnt=len(specs):
                    self._on_send_finished(peer_name, ok, reason, "File", tot, cnt)
            )
            self._submit_task(task)
            sent += 1
        if sent:
            self.notify(f"Sending to {sent} peer(s)…")

    # ---------- send text ----------
    def _on_send_text(self, peer_names: list, text: str) -> None:
        sent = 0
        for name in peer_names:
            peer = self.registry.find_by_name(name)
            if peer is None or peer.status != "online":
                continue
            task = self._new_task(peer, "text", text=text)
            task.finished.connect(
                lambda peer_name, ok, reason:
                    self._on_send_finished(peer_name, ok, reason, "QuickText", 0, 0)
            )
            self._submit_task(task)
            sent += 1
        if sent:
            self.notify(f"Quick text sent to {sent} peer(s)")

    def _on_send_finished(self, peer_name, ok, reason, kind, size, count) -> None:
        if ok:
            entry = {
                "date": _now(),
                "size": size if kind == "File" else 0,
                "count": count if kind == "File" else 0,
                "direction": "Sent",
                "peer": peer_name,
                "type": kind,
            }
            storage.append_history(entry)
            self.tab_history.append(entry)
            self.notify(f"Sent to {peer_name}")
        else:
            if str(reason).strip().lower() == "offline":
                return
            self.notify(f"Failed to {peer_name}: {reason}")

    # ---------- receive ----------
    @pyqtSlot(object)
    def _on_offer_received(self, offer) -> None:
        if not self.settings["online"] or offer.sender_id in self._muted:
            offer.respond(False)
            return
        # Impersonation warning: same display name, different cert fingerprint.
        known_id = next(
            (p.peer_id for p in self.registry.peers.values() if p.name == offer.sender_name),
            None,
        )
        if known_id and offer.sender_id and known_id != offer.sender_id:
            self.notify(f"⚠ Impersonation attempt: \u201c{offer.sender_name}\u201d (different fingerprint)")
        dlg = AcceptOfferDialog(offer, self)
        ok = bool(dlg.exec())
        offer.respond(ok, dlg.pin() if ok else "")
        if ok:
            self.notify(f"Accepting from {offer.sender_name}…")

    @pyqtSlot(str, str, int, int, float, str)
    def _on_recv_file_progress(self, sender, filename, done, total, bps, eta) -> None:
        self.tab_transfer.on_recv_progress(sender, filename, done, total, bps, eta)
        pct = int(done * 100 / total) if total else 0
        self.statusBar().showMessage(
            f"Receiving from {sender}: {filename} {pct}% @ {bps/1024/1024:.1f} MB/s ETA {eta}",
            1500,
        )

    @pyqtSlot(str, list, int)
    def _on_recv_completed(self, sender, filenames, total_bytes) -> None:
        entry = {
            "date": _now(),
            "size": total_bytes, "count": len(filenames),
            "direction": "Received", "peer": sender, "type": "File",
        }
        storage.append_history(entry)
        self.tab_history.append(entry)
        self.tab_transfer.on_recv_completed(sender)
        self.notify(f"Received {len(filenames)} file(s) from {sender}")

    @pyqtSlot(str, str)
    def _on_text_received(self, sender, text) -> None:
        items = storage.load_quicktexts()
        items.append({"sender": sender, "text": text, "date": _now()})
        storage.save_quicktexts(items)
        self.tab_text.add_received(sender, text)

        hist = {
            "date": _now(),
            "size": 0, "count": 0,
            "direction": "Received", "peer": sender, "type": "QuickText",
        }
        storage.append_history(hist)
        self.tab_history.append(hist)
        self.notify(f"Quick text received from {sender}")

    # ---------- folder sync ----------
    def _on_sync_start(self, peer_name: str, folder: str) -> None:
        if self.sync_sender is not None:
            QMessageBox.information(self, "Sync", "A sync is already running.")
            return
        peer = self.registry.find_by_name(peer_name)
        if peer is None or peer.status != "online":
            QMessageBox.warning(self, "Sync", "Peer not available.")
            return
        try:
            sock = connect_and_offer(peer.address, peer.port, {
                "type": "offer", "kind": "sync",
                "from": self.settings["device_name"],
                "from_id": self.registry.peer_id,
                "folder": Path(folder).name,
            })
        except Exception as e:
            QMessageBox.warning(self, "Sync", f"Could not start sync: {e}")
            return

        self.sync_sender = SyncSender(sock, folder)
        self.sync_sender.event.connect(lambda m: self.tab_tools.set_sync_running(True, m))
        self.sync_sender.finished.connect(self._on_sync_sender_done)
        self.sync_sender.start()
        self.tab_tools.set_sync_running(True, "starting…")
        self.notify(f"Folder syncing to {peer_name}")

    def _on_sync_stop(self) -> None:
        if self.sync_sender:
            self.sync_sender.stop(notify=True)
        if self.sync_receiver:
            self.sync_receiver.stop(notify=True)

    def _on_sync_sender_done(self, reason: str) -> None:
        self.sync_sender = None
        self.tab_tools.set_sync_running(False, f"stopped ({reason})")
        self.notify("Sync stopped")

    def _on_incoming_sync(self, sender_name: str, folder: str, sock) -> None:
        dest = QFileDialog.getExistingDirectory(
            self, f"Select destination for sync from {sender_name}",
            self.settings["download_dir"],
        )
        if not dest:
            try:
                sock.close()
            except Exception:
                pass
            return
        self.sync_receiver = SyncReceiver(sock, dest)
        self.sync_receiver.event.connect(lambda m: self.tab_tools.set_sync_running(True, m))
        self.sync_receiver.finished.connect(self._on_sync_receiver_done)
        self.sync_receiver.start()
        self.tab_tools.set_sync_running(True, "receiving")
        self.notify(f"Folder sync from {sender_name} started")

    def _on_sync_receiver_done(self, reason: str) -> None:
        self.sync_receiver = None
        self.tab_tools.set_sync_running(False, f"stopped ({reason})")

    # ---------- QR web server ----------
    def _on_qr_start(self) -> None:
        if self.qr_server is not None:
            return
        self.qr_server = QrWebServer(self.settings["device_name"], self.settings["download_dir"])
        self.qr_server.started.connect(self.tab_tools.show_qr)
        self.qr_server.stopped.connect(self.tab_tools.hide_qr)
        self.qr_server.file_received.connect(self._on_web_file_received)
        self.qr_server.text_received.connect(self._on_text_received)
        self.qr_server.start()
        self.notify("QR web server running")

    def _on_qr_stop(self) -> None:
        if self.qr_server:
            self.qr_server.stop()
            self.qr_server = None
            self.notify("QR web server stopped")

    def _on_web_file_received(self, filename: str, path: str, size: int) -> None:
        entry = {
            "date": _now(),
            "size": size, "count": 1,
            "direction": "Received", "peer": "Phone (QR)", "type": "File",
        }
        storage.append_history(entry)
        self.tab_history.append(entry)
        self.notify(f"Phone uploaded {filename}")

    # ---------- shutdown ----------
    def closeEvent(self, e) -> None:
        for fn in (self.server.stop, self.registry.stop):
            try:
                fn()
            except Exception:
                pass
        if self.qr_server:
            try:
                self.qr_server.stop()
            except Exception:
                pass
        if self.sync_sender:
            self.sync_sender.stop(notify=False)
        if self.sync_receiver:
            self.sync_receiver.stop(notify=False)
        super().closeEvent(e)
