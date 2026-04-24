"""Tab 2: Quick Text."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .dialogs import QuickTextEditor, QuickTextReader
from .widgets import PeerListPair


def _title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "h2")
    return lbl


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    return lbl


class QuickTextTab(QWidget):
    send_text_requested = pyqtSignal(list, str)   # peers, text
    mute_toggled = pyqtSignal(str)                # peer name
    inbox_changed = pyqtSignal(list)              # full inbox (oldest-first, ready to save)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._inbox: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(16)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)

        # ---------- Peers card ----------
        peers_card = QFrame()
        peers_card.setObjectName("card")
        pv = QVBoxLayout(peers_card)
        pv.setContentsMargins(18, 16, 18, 16)
        pv.setSpacing(12)
        pv.addWidget(_title("Peers"))

        self.peers = PeerListPair(list_min_height=140)
        self.discovered_list = self.peers.discovered
        self.selected_list = self.peers.selected
        self.peers.peer_right_clicked.connect(self.mute_toggled.emit)
        pv.addWidget(self.peers, 1)

        write_btn = QPushButton("Write Quick Text")
        write_btn.setProperty("role", "primary")
        write_btn.setMinimumHeight(38)
        write_btn.clicked.connect(self._write)
        pv.addWidget(write_btn)

        # ---------- Inbox card ----------
        inbox_card = QFrame()
        inbox_card.setObjectName("card")
        iv = QVBoxLayout(inbox_card)
        iv.setContentsMargins(18, 16, 18, 16)
        iv.setSpacing(12)
        iv.addWidget(_title("Inbox"))
        iv.addWidget(_muted("Double-click to open · Right-click to delete"))
        self.inbox_list = QListWidget()
        self.inbox_list.itemDoubleClicked.connect(self._open_received)
        self.inbox_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.inbox_list.customContextMenuRequested.connect(self._on_inbox_rightclick)
        iv.addWidget(self.inbox_list, 1)

        self.clear_inbox_btn = QPushButton("Clear Inbox")
        self.clear_inbox_btn.setProperty("role", "danger")
        self.clear_inbox_btn.clicked.connect(self._on_clear_inbox)
        iv.addWidget(self.clear_inbox_btn)

        split.addWidget(peers_card)
        split.addWidget(inbox_card)
        split.setSizes([620, 380])
        root.addWidget(split, 1)

    # ---------- peer list (delegates to PeerListPair) ----------
    def upsert_peer(self, peer) -> None:
        self.peers.upsert_peer(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.peers.remove_peer(peer_id)

    # ---------- send ----------
    def _write(self) -> None:
        peers = [self.selected_list.item(i).text() for i in range(self.selected_list.count())]
        if not peers:
            return
        dlg = QuickTextEditor(self)
        if dlg.exec():
            text = dlg.text()
            if text:
                self.send_text_requested.emit(peers, text)

    # ---------- inbox ----------
    def add_received(self, sender: str, text: str) -> None:
        self._inbox.insert(0, {"sender": sender, "text": text})
        preview = text[:50].replace("\n", " ")
        item = QListWidgetItem(f"{sender}   ·   {preview}")
        self.inbox_list.insertItem(0, item)

    def load_inbox(self, items: list[dict]) -> None:
        self._inbox = list(reversed(items))
        self.inbox_list.clear()
        for e in self._inbox:
            preview = e["text"][:50].replace("\n", " ")
            self.inbox_list.addItem(f"{e['sender']}   ·   {preview}")

    def _open_received(self, item: QListWidgetItem) -> None:
        row = self.inbox_list.row(item)
        if 0 <= row < len(self._inbox):
            e = self._inbox[row]
            QuickTextReader(e["sender"], e["text"], self).exec()

    def _on_inbox_rightclick(self, pos) -> None:
        item = self.inbox_list.itemAt(pos)
        if item is None:
            return
        row = self.inbox_list.row(item)
        if not (0 <= row < len(self._inbox)):
            return
        sender = self._inbox[row]["sender"]
        resp = QMessageBox.question(
            self, "Delete message",
            f"Delete this message from {sender}?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if resp != QMessageBox.StandardButton.Ok:
            return
        self._inbox.pop(row)
        self.inbox_list.takeItem(row)
        self.inbox_changed.emit(list(reversed(self._inbox)))

    def _on_clear_inbox(self) -> None:
        if not self._inbox:
            return
        resp = QMessageBox.question(
            self, "Clear inbox",
            "Delete all received quick texts?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if resp != QMessageBox.StandardButton.Ok:
            return
        self._inbox.clear()
        self.inbox_list.clear()
        self.inbox_changed.emit([])
