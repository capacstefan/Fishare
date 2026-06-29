"""Tab 2: Quick Text."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from .dialogs import QuickTextEditor, QuickTextReader
from ._widgets import PeerSelector, confirm, h2, muted


def _preview(sender: str, text: str) -> str:
    return f"{sender}   ·   {text[:50].replace(chr(10), ' ')}"


def _card() -> QFrame:
    f = QFrame(); f.setObjectName("card"); return f


class QuickTextTab(QWidget):
    send_text_requested = pyqtSignal(list, str)
    mute_toggled = pyqtSignal(str)
    inbox_changed = pyqtSignal(list)  # oldest-first (storage order)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._inbox: list[dict] = []  # newest-first (display order)

        root = QVBoxLayout(self); root.setContentsMargins(4, 4, 4, 4); root.setSpacing(16)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14); split.setChildrenCollapsible(False)

        # ---- Peers ----
        peers = _card()
        pv = QVBoxLayout(peers); pv.setContentsMargins(18, 16, 18, 16); pv.setSpacing(12)
        pv.addWidget(h2("Peers"))
        self.selector = PeerSelector()
        self.selector.mute_toggled.connect(self.mute_toggled.emit)
        pv.addWidget(self.selector, 1)

        write = QPushButton("Write Quick Text")
        write.setProperty("role", "primary"); write.setMinimumHeight(42)
        write.clicked.connect(self._write)
        pv.addWidget(write)

        # ---- Inbox ----
        inbox = _card()
        iv = QVBoxLayout(inbox); iv.setContentsMargins(18, 16, 18, 16); iv.setSpacing(12)
        iv.addWidget(h2("Inbox"))
        iv.addWidget(muted("Double-click to open · Right-click to delete"))

        self.inbox_list = QListWidget()
        self.inbox_list.itemDoubleClicked.connect(self._open)
        self.inbox_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.inbox_list.customContextMenuRequested.connect(self._rightclick)
        iv.addWidget(self.inbox_list, 1)

        clear_btn = QPushButton("Clear Inbox")
        clear_btn.setProperty("role", "danger")
        clear_btn.clicked.connect(self._clear)
        iv.addWidget(clear_btn)

        split.addWidget(peers); split.addWidget(inbox)
        split.setSizes([620, 380])
        root.addWidget(split, 1)

    # ---- peer feed ----
    def upsert_peer(self, peer) -> None:
        self.selector.upsert_peer(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.selector.remove_peer(peer_id)

    # ---- send ----
    def _write(self) -> None:
        peers = self.selector.selected_names()
        if not peers:
            return
        dlg = QuickTextEditor(self)
        if dlg.exec():
            text = dlg.text()
            if text:
                self.send_text_requested.emit(peers, text)

    # ---- inbox ----
    def load_inbox(self, items: list[dict]) -> None:
        # storage is oldest-first; display newest-first.
        self._inbox = list(reversed(items))
        self.inbox_list.clear()
        for e in self._inbox:
            self.inbox_list.addItem(_preview(e["sender"], e["text"]))

    def add_received(self, sender: str, text: str) -> None:
        self._inbox.insert(0, {"sender": sender, "text": text})
        self.inbox_list.insertItem(0, _preview(sender, text))

    def _open(self, item: QListWidgetItem) -> None:
        row = self.inbox_list.row(item)
        if 0 <= row < len(self._inbox):
            e = self._inbox[row]
            QuickTextReader(e["sender"], e["text"], self).exec()

    def _rightclick(self, pos) -> None:
        item = self.inbox_list.itemAt(pos)
        if item is None:
            return
        row = self.inbox_list.row(item)
        if not (0 <= row < len(self._inbox)):
            return
        sender = self._inbox[row]["sender"]
        if not confirm(self, "Delete message", f"Delete this message from {sender}?"):
            return
        self._inbox.pop(row)
        self.inbox_list.takeItem(row)
        self.inbox_changed.emit(list(reversed(self._inbox)))

    def _clear(self) -> None:
        if not self._inbox or not confirm(self, "Clear inbox", "Delete all received quick texts?"):
            return
        self._inbox.clear()
        self.inbox_list.clear()
        self.inbox_changed.emit([])
