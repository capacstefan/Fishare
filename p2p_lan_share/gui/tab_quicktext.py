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
from .peer_list import PeerList
from ._widgets import confirm as _confirm, h2 as _h2, muted as _muted


class QuickTextTab(QWidget):
    send_text_requested = pyqtSignal(list, str)
    mute_toggled = pyqtSignal(str)
    inbox_changed = pyqtSignal(list)  # full inbox (oldest-first, ready to save)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._inbox: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(16)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)

        # Peers card
        peers_card = QFrame()
        peers_card.setObjectName("card")
        pv = QVBoxLayout(peers_card)
        pv.setContentsMargins(18, 16, 18, 16)
        pv.setSpacing(12)
        pv.addWidget(_h2("Peers"))

        cols = QHBoxLayout()
        cols.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_muted("Discovered"))
        self.discovered_list = PeerList(mute_on_right_click=True)
        self.discovered_list.setMinimumHeight(80)
        self.discovered_list.mute_requested.connect(self.mute_toggled.emit)
        self.discovered_list.itemDoubleClicked.connect(self._on_discover_dclick)
        left.addWidget(self.discovered_list, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_muted("Selected"))
        self.selected_list = QListWidget()
        self.selected_list.setMinimumHeight(80)
        self.selected_list.itemDoubleClicked.connect(
            lambda it: self.selected_list.takeItem(self.selected_list.row(it))
        )
        right.addWidget(self.selected_list, 1)
        clr = QPushButton("Clear All")
        clr.clicked.connect(self.selected_list.clear)
        right.addWidget(clr)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        pv.addLayout(cols, 1)

        write_btn = QPushButton("Write Quick Text")
        write_btn.setProperty("role", "primary")
        write_btn.setMinimumHeight(42)
        write_btn.clicked.connect(self._write)
        pv.addWidget(write_btn)

        # Inbox card
        inbox_card = QFrame()
        inbox_card.setObjectName("card")
        iv = QVBoxLayout(inbox_card)
        iv.setContentsMargins(18, 16, 18, 16)
        iv.setSpacing(12)
        iv.addWidget(_h2("Inbox"))
        iv.addWidget(_muted("Double-click to open · Right-click to delete"))
        self.inbox_list = QListWidget()
        self.inbox_list.itemDoubleClicked.connect(self._open_received)
        self.inbox_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.inbox_list.customContextMenuRequested.connect(self._on_inbox_rightclick)
        iv.addWidget(self.inbox_list, 1)

        clear_inbox_btn = QPushButton("Clear Inbox")
        clear_inbox_btn.setProperty("role", "danger")
        clear_inbox_btn.clicked.connect(self._on_clear_inbox)
        iv.addWidget(clear_inbox_btn)

        split.addWidget(peers_card)
        split.addWidget(inbox_card)
        split.setSizes([620, 380])
        root.addWidget(split, 1)

    # ---- peer list ----
    def upsert_peer(self, peer) -> None:
        self.discovered_list.upsert(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.discovered_list.remove(peer_id)
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                self.selected_list.takeItem(i)
                break

    def _on_discover_dclick(self, item: QListWidgetItem) -> None:
        pid = PeerList.pid_of(item)
        name = PeerList.name_of(item)
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected_list.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected_list.addItem(it)

    # ---- send ----
    def _write(self) -> None:
        peers = [self.selected_list.item(i).text() for i in range(self.selected_list.count())]
        if not peers:
            return
        dlg = QuickTextEditor(self)
        if dlg.exec():
            text = dlg.text()
            if text:
                self.send_text_requested.emit(peers, text)

    # ---- inbox ----
    def add_received(self, sender: str, text: str) -> None:
        self._inbox.insert(0, {"sender": sender, "text": text})
        self.inbox_list.insertItem(0, _inbox_item_text(sender, text))

    def load_inbox(self, items: list[dict]) -> None:
        self._inbox = list(reversed(items))
        self.inbox_list.clear()
        for e in self._inbox:
            self.inbox_list.addItem(_inbox_item_text(e["sender"], e["text"]))

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
        if not _confirm(self, "Delete message", f"Delete this message from {sender}?"):
            return
        self._inbox.pop(row)
        self.inbox_list.takeItem(row)
        self.inbox_changed.emit(list(reversed(self._inbox)))

    def _on_clear_inbox(self) -> None:
        if not self._inbox:
            return
        if not _confirm(self, "Clear inbox", "Delete all received quick texts?"):
            return
        self._inbox.clear()
        self.inbox_list.clear()
        self.inbox_changed.emit([])


def _inbox_item_text(sender: str, text: str) -> str:
    preview = text[:50].replace("\n", " ")
    return f"{sender}   ·   {preview}"
