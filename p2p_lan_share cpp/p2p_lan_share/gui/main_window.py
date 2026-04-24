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
from ..network import FileSpec, TransferQueue, TransferServer, TransferTask
from ..sync import SyncReceiver, SyncSender
from ..web_server import QrWebServer
from .dialogs import AcceptOfferDialog
from .tab_history import HistoryTab
from .tab_quicktext import QuickTextTab
from .tab_tools import ToolsTab
from .tab_transfer import TransferTab


class NotificationBar(QLabel):
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
        self._hide_timer.timeout.connect(self._fade_out)

    def show_message(self, text: str, duration_ms: int = 2500) -> None:
        self.setText(text)
        self._anim.stop()
        self._anim.setDuration(300)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start(duration_ms)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(500)
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()


def _now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = storage.load_settings()
        # Mute is keyed on peer_id (cert fingerprint). Drop any legacy name-based
        # entries from older versions so they don't silently linger.
        self._muted: set[str] = {m for m in storage.load_muted() if len(m) == 16 and all(c in "0123456789abcdef" for c in m)}
        if self._muted != storage.load_muted():
            storage.save_muted(self._muted)

        self.setWindowTitle(config.APP_NAME)
        self.resize(1200, 780)
        self.setMinimumSize(980, 640)

        self.registry = PeerRegistry(self.settings["device_name"], bool(self.settings["online"]))
        self.registry.set_muted(self._muted)

        self.server = TransferServer(self._get_server_state)
        self.queue = TransferQueue()
        self.qr_server: QrWebServer | None = None
        self.sync_sender: SyncSender | None = None
        self.sync_receiver: SyncReceiver | None = None
        self._live_tasks: list = []

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

        self.tabs.addTab(self.tab_transfer, "File Transfer")
        self.tabs.addTab(self.tab_text, "Quick Text")
        self.tabs.addTab(self.tab_tools, "Tools")
        self.tabs.addTab(self.tab_history, "History")
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

    def _connect_signals(self) -> None:
        """Wire every cross-component signal in one place.

        Each row is ``(source, signal_name, slot)``. Adding a new wire is one
        line; reviewing the whole graph is one scan.
        """
        statusbar_log = lambda m: self.statusBar().showMessage(m, 4000)
        wires = [
            # Peer discovery -> GUI update.
            (self.registry, "peer_added",   self._on_peer_added),
            (self.registry, "peer_updated", self._on_peer_updated),
            (self.registry, "peer_removed", self._on_peer_removed),

            # Transfer tab -> controller slots.
            (self.tab_transfer, "device_name_changed", self._on_device_name_changed),
            (self.tab_transfer, "online_toggled",      self._on_online_toggled),
            (self.tab_transfer, "choose_download_dir", self._pick_download_dir),
            (self.tab_transfer, "send_requested",      self._on_send_files),
            (self.tab_transfer, "mute_toggled",        self._on_toggle_mute),

            # Quick Text tab.
            (self.tab_text, "send_text_requested", self._on_send_text),
            (self.tab_text, "mute_toggled",        self._on_toggle_mute),
            (self.tab_text, "inbox_changed",       storage.save_quicktexts),

            # Tools tab (sync + QR).
            (self.tab_tools, "sync_start_requested", self._on_sync_start),
            (self.tab_tools, "sync_stop_requested",  self._on_sync_stop),
            (self.tab_tools, "qr_start_requested",   self._on_qr_start),
            (self.tab_tools, "qr_stop_requested",    self._on_qr_stop),

            # TLS transfer server -> GUI.
            (self.server, "offer_received",         self._on_offer_received),
            (self.server, "file_progress",          self._on_recv_file_progress),
            (self.server, "transfer_completed",     self._on_recv_completed),
            (self.server, "recv_failed",            self.tab_transfer.on_recv_failed),
            (self.server, "text_received",          self._on_text_received),
            (self.server, "sync_started",           self._on_incoming_sync),
            (self.server, "impersonation_detected", self._on_impersonation_detected),
            (self.server, "log",                    statusbar_log),
        ]
        for source, name, slot in wires:
            getattr(source, name).connect(slot)

    def _refresh_status(self) -> None:
        self.statusBar().showMessage(
            f"Device: {self.settings['device_name']}  |  "
            f"{'Online' if self.settings['online'] else 'Offline'}  |  "
            f"Downloads: {self.settings['download_dir']}"
        )

    def _get_server_state(self) -> dict:
        # known_ids_by_name lets the server detect impersonation (same display
        # name, different stable fingerprint). Mute is keyed on peer_id.
        return {
            "online": bool(self.settings["online"]),
            "muted": set(self._muted),
            "download_dir": self.settings["download_dir"],
            "known_ids_by_name": {p.name: p.peer_id for p in self.registry.peers.values()},
        }

    def notify(self, text: str) -> None:
        self.notification.show_message(text)

    @pyqtSlot(object)
    def _on_peer_added(self, peer) -> None:
        self.tab_transfer.upsert_peer(peer)
        self.tab_text.upsert_peer(peer)
        self.tab_tools.upsert_peer(peer)

    @pyqtSlot(object)
    def _on_peer_updated(self, peer) -> None:
        self.tab_transfer.upsert_peer(peer)
        self.tab_text.upsert_peer(peer)
        self.tab_tools.upsert_peer(peer)
        if peer.status == "offline":
            self.tab_transfer.remove_offline_selected(peer.peer_id)

    @pyqtSlot(str)
    def _on_peer_removed(self, peer_id: str) -> None:
        self.tab_transfer.remove_peer(peer_id)
        self.tab_text.remove_peer(peer_id)
        self.tab_tools.remove_peer(peer_id)

    def _on_device_name_changed(self, name: str) -> None:
        name = name or config.default_device_name()
        self.settings["device_name"] = name
        storage.save_settings(self.settings)
        self._refresh_status()
        QTimer.singleShot(0, lambda: self.registry.set_device_name(name))

    def _on_online_toggled(self, online: bool) -> None:
        # Update local state + UI immediately; defer the (blocking) mDNS
        # re-registration so the toggle animation stays smooth.
        self.settings["online"] = bool(online)
        storage.save_settings(self.settings)
        self._refresh_status()
        self.notify("Online" if online else "Offline")
        QTimer.singleShot(0, lambda: self.registry.set_online(bool(online)))

    def _pick_download_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Default Download Folder", self.settings["download_dir"]
        )
        if folder:
            self.settings["download_dir"] = folder
            storage.save_settings(self.settings)
            self._refresh_status()

    def _on_toggle_mute(self, peer_id: str) -> None:
        # peer_id = stable cert-fingerprint identity (survives rename).
        peer = self.registry.peers.get(peer_id)
        display = peer.name if peer else peer_id
        now_muted = self.registry.toggle_mute(peer_id)
        self._muted = self.registry.muted
        storage.save_muted(self._muted)
        self.notify(f"{'Muted' if now_muted else 'Unmuted'} {display}")

    @pyqtSlot(str)
    def _on_impersonation_detected(self, claimed_name: str) -> None:
        # Another peer tried to use a name we already know under a different
        # fingerprint. Warn the user — the real peer is unaffected.
        self.notify(f"⚠ Impersonation attempt: “{claimed_name}” (different fingerprint)")

    def _submit_task(self, task: TransferTask) -> None:
        self._live_tasks.append(task)
        task.finished.connect(
            lambda *a, t=task: (self._live_tasks.remove(t) if t in self._live_tasks else None)
        )
        self.queue.submit(task)

    def _on_send_files(self, peer_names: list, file_paths: list, pin: str) -> None:
        specs = []
        for p in file_paths:
            pth = Path(p)
            if pth.exists() and pth.is_file():
                size = pth.stat().st_size
                if size > config.MAX_FILE_SIZE:
                    QMessageBox.warning(
                        self, "File too large",
                        f"{pth.name} exceeds the 2 GB limit and will be skipped.",
                    )
                    continue
                specs.append(FileSpec(path=str(pth), size=size))
        if not specs:
            return

        total = sum(s.size for s in specs)
        for name in peer_names:
            peer = self.registry.find_by_name(name)
            if peer is None or peer.status != "online":
                self.tab_transfer.on_task_status(name, "offline")
                continue

            task = TransferTask(
                peer_name=name,
                peer_addr=peer.address,
                peer_port=peer.port,
                kind="files",
                from_name=self.settings["device_name"],
                from_id=self.registry.peer_id,
                files=specs,
                pin=pin,
            )
            task.progress.connect(self.tab_transfer.on_task_progress)
            task.status.connect(self.tab_transfer.on_task_status)
            task.finished.connect(
                lambda peer_name, ok, reason, tot=total, cnt=len(specs):
                    self._on_send_finished(peer_name, ok, reason, "File", tot, cnt)
            )
            self._submit_task(task)

        self.notify(f"Sending to {len(peer_names)} peer(s)â€¦")

    def _on_send_text(self, peer_names: list, text: str) -> None:
        sent_to = 0
        for name in peer_names:
            peer = self.registry.find_by_name(name)
            if peer is None or peer.status != "online":
                continue
            task = TransferTask(
                peer_name=name,
                peer_addr=peer.address,
                peer_port=peer.port,
                kind="text",
                from_name=self.settings["device_name"],
                from_id=self.registry.peer_id,
                text=text,
            )
            task.finished.connect(
                lambda peer_name, ok, reason:
                    self._on_send_finished(peer_name, ok, reason, "QuickText", 0, 0)
            )
            self._submit_task(task)
            sent_to += 1
        self.notify(f"Quick text sent to {sent_to} peer(s)")

    def _on_send_finished(self, peer_name: str, ok: bool, reason: str,
                          kind: str, size: int, count: int) -> None:
        if ok:
            entry = {
                "date": _now_str(),
                "size": size if kind == "File" else "-",
                "count": count if kind == "File" else "-",
                "direction": "Sent",
                "peer": peer_name,
                "type": kind,
            }
            storage.append_history(entry)
            self.tab_history.append(entry)
            self.notify(f"Sent to {peer_name}")
        else:
            self.notify(f"Failed to {peer_name}: {reason}")

    @pyqtSlot(object)
    def _on_offer_received(self, offer) -> None:
        if not self.settings["online"] or offer.sender_id in self._muted:
            offer.respond(False)
            return
        dlg = AcceptOfferDialog(offer, self)
        ok = bool(dlg.exec())
        offer.respond(ok, dlg.pin() if ok else "")
        if ok:
            self.notify(f"Accepting from {offer.sender_name}â€¦")

    @pyqtSlot(str, str, int, int, float, str)
    def _on_recv_file_progress(self, sender: str, filename: str, done: int,
                               total: int, bps: float, eta: str) -> None:
        # Drive the transfer tab row + keep a brief statusbar hint.
        self.tab_transfer.on_recv_progress(sender, filename, done, total, bps, eta)
        pct = int(done * 100 / total) if total else 0
        self.statusBar().showMessage(
            f"Receiving from {sender}: {filename} {pct}% @ {bps/1024/1024:.1f} MB/s ETA {eta}",
            1500,
        )

    @pyqtSlot(str, list, int)
    def _on_recv_completed(self, sender: str, filenames: list, total_bytes: int) -> None:
        entry = {
            "date": _now_str(),
            "size": total_bytes,
            "count": len(filenames),
            "direction": "Received",
            "peer": sender,
            "type": "File",
        }
        storage.append_history(entry)
        self.tab_history.append(entry)
        self.tab_transfer.on_recv_completed(sender)
        self.notify(f"Received {len(filenames)} file(s) from {sender}")

    @pyqtSlot(str, str)
    def _on_text_received(self, sender: str, text: str) -> None:
        entry_inbox = {"sender": sender, "text": text, "date": _now_str()}
        items = storage.load_quicktexts()
        items.append(entry_inbox)
        storage.save_quicktexts(items)
        self.tab_text.add_received(sender, text)

        hist = {
            "date": _now_str(),
            "size": "-", "count": "-",
            "direction": "Received",
            "peer": sender,
            "type": "QuickText",
        }
        storage.append_history(hist)
        self.tab_history.append(hist)
        self.notify(f"Quick text received from {sender}")

    def _on_sync_start(self, peer_name: str, folder: str) -> None:
        if self.sync_sender is not None:
            QMessageBox.information(self, "Sync", "A sync is already running.")
            return
        peer = self.registry.find_by_name(peer_name)
        if peer is None or peer.status != "online":
            QMessageBox.warning(self, "Sync", "Peer not available.")
            return

        import socket as _socket
        from ..network import _client_ctx, _send_json, _tune_sock, LineReader  # type: ignore

        try:
            raw = _socket.create_connection((peer.address, peer.port), timeout=10)
            _tune_sock(raw)
            sock = _client_ctx().wrap_socket(raw, server_hostname=peer.address)
            _send_json(sock, {
                "type": "offer", "kind": "sync",
                "from": self.settings["device_name"],
                "from_id": self.registry.peer_id,
                "folder": Path(folder).name,
            })
            sock.settimeout(180)
            resp = LineReader(sock).read_json()
            if not resp.get("accept"):
                QMessageBox.information(
                    self, "Sync", f"Peer rejected sync: {resp.get('reason', '')}"
                )
                sock.close()
                return
        except Exception as e:
            QMessageBox.warning(self, "Sync", f"Could not start sync: {e}")
            return

        self.sync_sender = SyncSender(sock, folder)
        self.sync_sender.event.connect(lambda m: self.tab_tools.set_sync_running(True, m))
        self.sync_sender.finished.connect(self._on_sync_sender_done)
        self.sync_sender.start()
        self.tab_tools.set_sync_running(True, "startingâ€¦")
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
            "date": _now_str(),
            "size": size, "count": 1,
            "direction": "Received",
            "peer": "Phone (QR)",
            "type": "File",
        }
        storage.append_history(entry)
        self.tab_history.append(entry)
        self.notify(f"Phone uploaded {filename}")

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
