"""Tab 2: Quick Text."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .dialogs import QuickTextEditor, QuickTextReader


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

        cols = QHBoxLayout()
        cols.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_muted("Discovered"))
        self.discovered_list = QListWidget()
        self.discovered_list.setMinimumHeight(220)
        self.discovered_list.itemDoubleClicked.connect(self._on_discover_dclick)
        self.discovered_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.discovered_list.customContextMenuRequested.connect(self._on_discovered_rightclick)
        left.addWidget(self.discovered_list)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_muted("Selected"))
        self.selected_list = QListWidget()
        self.selected_list.setMinimumHeight(220)
        self.selected_list.itemDoubleClicked.connect(
            lambda it: self.selected_list.takeItem(self.selected_list.row(it))
        )
        right.addWidget(self.selected_list)
        clr = QPushButton("Clear All")
        clr.clicked.connect(self.selected_list.clear)
        right.addWidget(clr)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        pv.addLayout(cols)

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
        iv.addWidget(_muted("Double-click to open"))
        self.inbox_list = QListWidget()
        self.inbox_list.itemDoubleClicked.connect(self._open_received)
        iv.addWidget(self.inbox_list, 1)

        split.addWidget(peers_card)
        split.addWidget(inbox_card)
        split.setSizes([620, 380])
        root.addWidget(split, 1)

    # ---------- peer list ----------
    def upsert_peer(self, peer) -> None:
        for i in range(self.discovered_list.count()):
            it = self.discovered_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer.peer_id:
                it.setText(peer.display)
                it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
                return
        it = QListWidgetItem(peer.display)
        it.setData(Qt.ItemDataRole.UserRole, peer.peer_id)
        it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
        self.discovered_list.addItem(it)

    def remove_peer(self, peer_id: str) -> None:
        for i in range(self.discovered_list.count()):
            if self.discovered_list.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                self.discovered_list.takeItem(i)
                break
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                self.selected_list.takeItem(i)
                break

    def _on_discover_dclick(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected_list.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected_list.addItem(it)

    def _on_discovered_rightclick(self, pos) -> None:
        # Right-click on a discovered peer directly toggles mute.
        # Emit the stable peer_id (cert fingerprint).
        item = self.discovered_list.itemAt(pos)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.mute_toggled.emit(pid)

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
